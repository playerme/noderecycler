package main

import (
	"context"
	"fmt"
	"io/ioutil"
	"log"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"golang.org/x/oauth2/google"
	compute "google.golang.org/api/compute/v1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/api/policy/v1beta1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes"
	_ "k8s.io/client-go/plugin/pkg/client/auth/gcp"
	"k8s.io/client-go/rest"
	"k8s.io/client-go/tools/clientcmd"
)

const (
	defaultAge   = 12 * time.Hour
	defaultSleep = 10 * time.Minute
)

// Add a custom type for recycler
type Recycler struct {
	GCE        *compute.InstancesService
	Kubernetes *kubernetes.Clientset
	AgeToKill  time.Duration
	SleepTime  time.Duration
	Pod        *corev1.Pod
}

// Initialize a GCE Instance service to perform instances operations
func initGCEInstanceService(filename string) (*compute.InstancesService, error) {
	ctx := context.Background()
	data, err := ioutil.ReadFile(filename)
	if err != nil {
		return nil, err
	}
	config, err := google.JWTConfigFromJSON(data, compute.ComputeScope)
	if err != nil {
		return nil, err
	}
	computeService, err := compute.New(config.Client(ctx))
	if err != nil {
		return nil, err
	}
	return computeService.Instances, nil
}

func initKubernetes() (*kubernetes.Clientset, error) {
	var cfg *rest.Config
	var err error
	if os.Getenv("KUBERNETES_SERVICE_HOST") == "" {
		log.Println("Running Outside a Kubernetes Cluster")
		configfile := filepath.Join(os.Getenv("HOME"), ".kube", "config")
		cfg, err = clientcmd.BuildConfigFromFlags("", configfile)
		if err != nil {
			return nil, err
		}
	} else {
		log.Println("Running Inside a Kubernetes Cluster")
		cfg, err = rest.InClusterConfig()
		if err != nil {
			return nil, err
		}
	}

	clientset, err := kubernetes.NewForConfig(cfg)
	if err != nil {
		return nil, err
	}
	return clientset, nil
}

func NewRecycler(f string, a time.Duration,
	s time.Duration, n string, ns string) *Recycler {
	g, err := initGCEInstanceService(f)
	if err != nil {
		log.Fatal(err)
	}
	k, err := initKubernetes()
	if err != nil {
		log.Fatal(err)
	}
	p, err := k.CoreV1().Pods(ns).Get(n, metav1.GetOptions{})
	if err != nil {
		log.Fatal(err)
	}
	return &Recycler{g, k, a, s, p}
}

// Delete an instance on GCE
func (r *Recycler) deleteInstance(n *corev1.Node) (*compute.Operation, error) {
	// Get the providerId and split it using / as separator
	// It has the following format
	// gce://<PROJECT>/<ZONE>/<INSTANCE_NAME>
	p := strings.Split(n.Spec.ProviderID, "/")
	op, err := r.GCE.Delete(p[2], p[3], p[4]).Do()
	if err != nil {
		return nil, err
	}
	return op, nil
}

func (r *Recycler) getNodes() (*corev1.NodeList, error) {
	log.Println("Listing available nodes in the cluster")
	nodes, err := r.Kubernetes.CoreV1().Nodes().List(metav1.ListOptions{})
	if err != nil {
		return nil, err
	}
	return nodes, nil

}

// Returns a list of nodes to be killed - preemptible ones
func (r *Recycler) getKillableNodes() ([]corev1.Node, error) {
	var kn []corev1.Node
	nl, err := r.getNodes()
	if err != nil {
		return nil, err
	}
	log.Println("Getting nodes that can be recycled (preemptibles)")
	for _, n := range nl.Items {
		if _, present := n.Labels["cloud.google.com/gke-preemptible"]; present {
			kn = append(kn, n)
		}

	}
	return kn, nil
}

// Return the oldest killable node
func (r *Recycler) getElderNode() (*corev1.Node, error) {
	n, err := r.getKillableNodes()
	if err != nil {
		return nil, err
	}
	log.Println("Getting the oldest node")
	sort.Slice(n, func(i, j int) bool {
		return n[i].CreationTimestamp.Time.UnixNano() < n[j].CreationTimestamp.Time.UnixNano()

	})
	node := &n[0]
	log.Printf("Oldest node is %s", node.Name)
	return node, nil
}

// Cordon the node to be killed
func (r *Recycler) cordonNode(n *corev1.Node) error {
	if n.Spec.Unschedulable {
		log.Println("Node already cordoned")
		return nil
	}
	log.Println("Cordoning Node")
	n.Spec.Unschedulable = true
	_, err := r.Kubernetes.CoreV1().Nodes().Update(n)
	if err != nil {
		return err
	}
	return nil
}

func (r *Recycler) getPodsOnNode(n *corev1.Node) (*corev1.PodList, error) {
	listOptions := metav1.ListOptions{
		FieldSelector: fmt.Sprintf("spec.nodeName=%s", n.Name),
	}
	log.Println("Listing pods in the node")
	pods, err := r.Kubernetes.CoreV1().Pods(corev1.NamespaceAll).List(listOptions)
	if err != nil {
		return nil, err
	}
	return pods, nil
}

func (r *Recycler) isMyNode(n *corev1.Node) bool {
	return r.Pod.Spec.NodeName == n.Name
}

func (r *Recycler) evictPod(p *corev1.Pod) {
	e := &v1beta1.Eviction{ObjectMeta: p.ObjectMeta}
	log.Printf("Evicting pod %s/%s", p.Namespace, p.Name)
	err := r.Kubernetes.CoreV1().Pods(p.Namespace).Evict(e)
	if err != nil {
		log.Println(err)
	}
}

func (r *Recycler) drainNode(n *corev1.Node) {
	log.Println("Preparing to drain node")
	pods, err := r.getPodsOnNode(n)
	if err != nil {
		log.Println(err)
		return
	}
	for _, p := range pods.Items {
		r.evictPod(&p)
	}
}

func (r *Recycler) deleteNode(n *corev1.Node) {
	log.Println("Removing node from cluster")
	err := r.Kubernetes.CoreV1().Nodes().Delete(n.Name, &metav1.DeleteOptions{})
	if err != nil {
		log.Println(err)
	} else {
		log.Println("Node removed")
	}
}

func (r *Recycler) recycleNode(n *corev1.Node) {
	log.Println("Preparing to recycle node")
	r.cordonNode(n)
	if r.isMyNode(n) {
		log.Println("This is my node. Evicting myself. Bye!")
		r.evictPod(r.Pod)
		os.Exit(0)
	}
	r.drainNode(n)
	r.deleteNode(n)
	r.deleteInstance(n)
	log.Println("Node has been recycled")
}
func main() {
	var (
		a, s time.Duration
		err  error
	)
	keyfile, ok := os.LookupEnv("GCE_CREDENTIALS")
	if !ok {
		log.Fatal("GCE_CREDENTIALS not defined")
	}
	n, ok := os.LookupEnv("POD_NAME")
	if !ok {
		log.Fatal("POD_NAME not defined")
	}
	ns, ok := os.LookupEnv("NAMESPACE")
	if !ok {
		log.Fatal("NAMESPACE not defined")
	}
	age, ok := os.LookupEnv("AGE_TO_KILL")
	if !ok {
		log.Println("AGE_TO_KILL not defined. Using default values")
		a = defaultAge
	} else {
		a, err = time.ParseDuration(age)
		if err != nil {
			log.Fatal(err)
		}
	}

	sleep, ok := os.LookupEnv("SLEEP_TIME")
	if !ok {
		log.Println("SLEEP_TIME not defined. Using default values")
		s = defaultSleep
	} else {
		s, err = time.ParseDuration(sleep)
		if err != nil {
			log.Fatal(err)
		}
	}
	r := NewRecycler(keyfile, a, s, n, ns)
	for {
		n, err := r.getElderNode()
		if err != nil {
			log.Fatalln("Unable to get oldest node. Quitting")
			log.Fatalln(err)
		}
		a := time.Since(n.CreationTimestamp.Time)
		if a > r.AgeToKill {
			r.recycleNode(n)
		} else {
			log.Println("Node age is lower than the minimum age to be recycled.")
		}
		log.Println("Sleeping...")
		time.Sleep(r.SleepTime)

	}
}
