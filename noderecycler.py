import os
import shlex
import subprocess
import logging
from time import mktime, sleep
from datetime import datetime
from kubernetes import client, config

logging.basicConfig(level=logging.os.environ.get('LOG_LEVEL', 'INFO'))
AGE_TO_KILL = int(os.environ.get('AGE_TO_KILL', 12))
SLEEP_TIME = int(os.environ.get('SLEEP_TIME', 10))


class Kubernetes():
    def __init__(self):
        """
        Load kubernetes configuration based if instance is running
        in a cluster or not
        """
        if 'KUBERNETES_SERVICE_HOST' in os.environ:
            logging.info('Running inside a kubernetes cluster')
            config.load_incluster_config()
        else:
            logging.info('Running outside a kubernetes cluster')
            config.load_kube_config()
        self.v1 = client.CoreV1Api()

    def get_nodes(self):
        logging.info('listing nodes')
        nodes = self.v1.list_node()
        return nodes.items

    def get_node_info(self, node):
        metadata = node.metadata
        creation_time = metadata.creation_timestamp
        name = metadata.name
        zone = metadata.labels['failure-domain.beta.kubernetes.io/zone']
        if 'cloud.google.com/gke-preemptible' in metadata.labels.keys():
            preemptible = True
        else:
            preemptible = False
        return {'name': name,
                'zone': zone,
                'preemptible': preemptible,
                'cordoned': node.spec.unschedulable,
                'creation_time': mktime(creation_time.timetuple())}

    def get_node_ages(self):
        now = mktime(datetime.utcnow().timetuple())
        nodes = []
        for node in self.get_nodes():
            node_info = self.get_node_info(node)
            if node_info['preemptible']:
                node_info['age'] = now - node_info['creation_time']
                nodes.append(node_info)
        return sorted(nodes, key=lambda n: n['age'])

    def cordon_node(self, node):
        if self.get_node_info(node)['cordoned']:
            logging.info('node already cordoned')
        else:
            logging.info('cordoning node')
            patch = {
                    'spec': {
                        'unschedulable': True
                        }
                    }
            self.v1.patch_node(node.metadata.name, patch)

    def is_my_node(self, node):
        me = {
                'name': os.environ['POD_NAME'],
                'namespace': os.environ['NAMESPACE']
             }
        pod_info = self.v1.read_namespaced_pod(me['name'], me['namespace'])
        if node.metadata.name == pod_info.spec.node_name:
            return True
        else:
            logging.info('not my node - proceed to kill')
            return False


def drain_node(node_name):
    cmdline = 'kubectl drain {} '\
            '--grace-period=180 '\
            '--ignore-daemonsets '\
            '--force --delete-local-data'.format(node_name)
    cmd = shlex.split(cmdline)
    logging.info('draining node')
    subprocess.call(cmd)


def remove_node_from_cluster(node_name):
    cmdline = 'kubectl delete node {}'.format(node_name)
    cmd = shlex.split(cmdline)
    logging.info('removing node from cluster')
    subprocess.call(cmd)


def delete_vm(node):
    cmdline = 'gcloud compute instances delete '\
            '{} --zone {} --quiet'.format(node['name'], node['zone'])
    cmd = shlex.split(cmdline)
    logging.info('deleting vm')
    subprocess.call(cmd)


def kill_node(node):
    cordon_node(node['name'])
    is_my_node(node['name'])
    drain_node(node['name'])
    remove_node_from_cluster(node['name'])
    delete_vm(node)
    logging.info('node killed. time to sleep')


if __name__ == '__main__':
    kube = Kubernetes()
    while True:
        elder_node = get_node_ages(get_nodes())[-1]
        if elder_node['age'] > (AGE_TO_KILL * 60 * 60):
            logging.info('node {} is too old and '
                         'will be killed'.format(elder_node['name']))
            kill_node(elder_node)
        else:
            logging.info('all nodes are too young to die, will sleep now')
        sleep(SLEEP_TIME * 60)
