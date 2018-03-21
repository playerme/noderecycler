import os
import sys
import shlex
import subprocess
import json
import dateutil.parser
import logging
from time import mktime, sleep
from datetime import datetime
from socket import gethostname

logging.basicConfig(level=logging.os.environ.get('LOG_LEVEL', 'INFO'))
AGE_TO_KILL = int(os.environ.get('AGE_TO_KILL', 12))
SLEEP_TIME = int(os.environ.get('SLEEP_TIME', 10))


def get_nodes():
    logging.info('using kubectl to get node list')
    cmd = shlex.split("kubectl get node -o json")
    output = subprocess.check_output(cmd)
    nodes = json.loads(output)
    return nodes['items']


def get_node_info(node):
    metadata = node['metadata']
    creation_time = dateutil.parser.parse(metadata['creationTimestamp'])
    name = metadata['name']
    zone = metadata['labels']['failure-domain.beta.kubernetes.io/zone']
    if 'cloud.google.com/gke-preemptible' in metadata['labels'].keys():
        preemptible = True
    else:
        preemptible = False
    return {'name': name,
            'zone': zone,
            'preemptible': preemptible,
            'creation_time': mktime(creation_time.timetuple())}


def get_node_ages(node_list):
    now = mktime(datetime.utcnow().timetuple())
    nodes = []
    for node in node_list:
        node_info = get_node_info(node)
        if node_info['preemptible']:
            node_info['age'] = now - node_info['creation_time']
            nodes.append(node_info)
    return sorted(nodes, key=lambda n: n['age'])


def get_node_status(node_name):
    cmd = shlex.split('kubectl get node {}'.format(node_name))
    output = subprocess.check_output(cmd)
    status = output.split('\n')[1].split()[1].split(',')
    return status


def cordon_node(node_name):
    if 'SchedulingDisabled' in get_node_status(node_name):
        logging.info('node already cordoned')
    else:
        logging.info('cordoning node')
        cmd = shlex.split('kubectl cordon {}'.format(node_name))
        subprocess.call(cmd)


def is_my_node(node_name):
    me = gethostname()
    cmd = shlex.split('kubectl get pod {} -o wide'.format(me))
    output = subprocess.check_output(cmd)
    my_node = output.split('\n')[1].split()[-1]
    if node_name == my_node:
        logging.info('this is my node - i should die')
        cmd = shlex.split('kubectl delete pod {}'.format(me))
        subprocess.call(cmd)
        sys.exit(1)
    else:
        logging.info('not my node - proceed to kill')


def drain_node(node_name):
    cmdline = 'kubectl drain {} --grace-period=180 --ignore-daemonsets --force --delete-local-data'.format(node_name)
    cmd = shlex.split(cmdline)
    logging.info('draining node')
    subprocess.call(cmd)


def remove_node_from_cluster(node_name):
    cmdline = 'kubectl delete node {}'.format(node_name)
    cmd = shlex.split(cmdline)
    logging.info('removing node from cluster')
    subprocess.call(cmd)


def delete_vm(node):
    cmdline = 'gcloud compute instances delete {} --zone {} --quiet'.format(node['name'], node['zone'])
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
    while True:
        elder_node = get_node_ages(get_nodes())[-1]
        if elder_node['age'] > (AGE_TO_KILL * 60 * 60):
            logging.info('node {} is too old and will be killed'.format(elder_node['name']))
            kill_node(elder_node)
        else:
            logging.info('all nodes are too young to die, will sleep now')
        sleep(SLEEP_TIME * 60)
