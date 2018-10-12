import os
import logging
import json
import requests
import kubernetes.client
import kubernetes.config
from time import mktime, sleep
from datetime import datetime
from libcloud.compute.types import Provider
from libcloud.compute.providers import get_driver

logging.basicConfig(level=logging.os.environ.get('LOG_LEVEL', 'INFO'))
AGE_TO_KILL = float(os.environ.get('AGE_TO_KILL', 12))
SLEEP_TIME = float(os.environ.get('SLEEP_TIME', 10))


class GCE():
    def __init__(self,
                 key_file=os.environ.get('GCE_CREDENTIALS'),
                 project=None):
        with open(key_file) as kf:
            svc_account = json.loads(kf.read())['client_email']
        engine = get_driver(Provider.GCE)
        if not project:
            project = self.get_project_from_metadata()
        self.driver = engine(svc_account, key_file, project=project)

    def get_project_from_metadata(self):
        headers = {'Metadata-Flavor': 'Google'}
        metadatabaseurl = 'http://metadata.google.internal/computeMetadata/v1'
        project_path = 'project/project-id'
        url = '{}/{}'.format(metadatabaseurl, project_path)
        req = requests.get(url, headers=headers)
        return req.text

    def get_vm(self, name, zone):
        node = self.driver.ex_get_node(name, zone=zone)
        return node


class Kubernetes():
    def __init__(self):
        """
        Load kubernetes configuration based if instance is running
        in a cluster or not
        """
        if 'KUBERNETES_SERVICE_HOST' in os.environ:
            logging.info('Running inside a kubernetes cluster')
            kubernetes.config.load_incluster_config()
        else:
            logging.info('Running outside a kubernetes cluster')
            kubernetes.config.load_kube_config()
        self.client = kubernetes.client
        self.v1 = self.client.CoreV1Api()
        self.gce = GCE()

    def get_node_age(self, node):
        now = mktime(datetime.utcnow().timetuple())
        node_timestamp = mktime(node.metadata.creation_timestamp.timetuple())
        age = now - node_timestamp
        return age

    def get_nodes(self):
        logging.info('listing nodes')
        nodes = self.v1.list_node()
        return nodes.items

    def get_killable_nodes(self):
        """
        This method return a list of nodes to be killed (preemptible ones)
        """
        killable_nodes = [
                n for n in self.get_nodes()
                if 'cloud.google.com/gke-preemptible' in n.metadata.labels
                ]
        return killable_nodes

    def get_elder_node(self):
        "This method return the oldest killable node"
        nodes = sorted(self.get_killable_nodes(), key=self.get_node_age)
        return nodes[-1]

    def cordon_node(self, node):
        if node.spec.unschedulable:
            logging.info('node already cordoned')
        else:
            logging.info('cordoning node')
            patch = {
                    'spec': {
                        'unschedulable': True
                        }
                    }
            self.v1.patch_node(node.metadata.name, patch)

    def get_pods_in_node(self, node):
        pods = [
                p for p in self.v1.list_pod_for_all_namespaces().items
                if p.spec.node_name == node.metadata.name
                ]
        return pods

    def evict_pod(self, pod):
        eviction_body = self.client.V1beta1Eviction(
                metadata=pod.metadata
                )
        logging.info('Evicting {}'.format(pod.metadata.name))
        eviction = self.v1.create_namespaced_pod_eviction(
                pod.metadata.name,
                pod.metadata.namespace,
                eviction_body)
        return eviction

    def is_my_node(self, node):
        me = {
                'name': os.environ['POD_NAME'],
                'namespace': os.environ['NAMESPACE']
             }
        pod = self.v1.read_namespaced_pod(me['name'], me['namespace'])
        if node.metadata.name == pod.spec.node_name:
            return pod
        else:
            return False

    def drain_node(self, node):
        logging.info('Draining node')
        for p in self.get_pods_in_node(node):
            self.evict_pod(p)

    def delete_node(self, node):
        logging.info('removing node from cluster')
        result = self.v1.delete_node(
                node.metadata.name,
                self.client.V1DeleteOptions()
                )
        return result

    def delete_vm(self, node):
        zone = node.metadata.labels['failure-domain.beta.kubernetes.io/zone']
        vm = self.gce.driver.ex_get_node(
                node.metadata.name,
                zone=zone
                )
        logging.info('Deleting VM')
        if vm.destroy():
            logging.info('VM deleteted successfully')
        else:
            logging.error('VM deletion failure')

    def kill_node(self, node):
        self.cordon_node(node)
        if self.is_my_node(node):
            return False
        self.drain_node(node)
        self.delete_node(node)
        self.delete_vm(node)
        return True

    def kill_self(self):
        self.evict_pod(self.is_my_node(self.get_elder_node()))


if __name__ == '__main__':
    kube = Kubernetes()
    while True:
        elder_node = kube.get_elder_node()
        if kube.get_node_age(elder_node) > (AGE_TO_KILL * 60 * 60):
            logging.info('node {} is too old and '
                         'will be killed'.format(elder_node.metadata.name))
            if kube.kill_node(elder_node):
                logging.info('node killed successfully, sleeping now')
            else:
                logging.info('this is my node, exiting')
                kube.kill_self()
        else:
            logging.info('all nodes are too young to die, will sleep now')
        sleep(SLEEP_TIME * 60)
