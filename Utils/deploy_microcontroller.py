from swiftclient import client as c
import os


def put_mc_object(url, token, mc_path, mc_name, main_class, dependency=''):

    metadata = {'X-Object-Meta-Function-Language': 'Java',
                'X-Object-Meta-Function-Interface-Version': '1.0',
                'X-Object-Meta-Function-Library-Dependency': dependency,
                'X-Object-Meta-Function-Main': main_class}
    f = open('%s/%s' % (mc_path, mc_name), 'r')
    content_length = os.stat(mc_path+'/'+mc_name).st_size
    response = dict()

    c.put_object(url, token, 'microcontroller', mc_name, f,
                 content_length, None, None,
                 "application/octet-stream", metadata,
                 None, None, None, response)
    f.close()
    status = response.get('status')
    assert (status == 200 or status == 201)


def put_mc_dependency(url, token, local_path_to_dep, dep_name):
    metadata = {'X-Object-Meta-Function-Dependency-Version': '1'}
    f = open('%s/%s' % (local_path_to_dep, dep_name), 'r')
    content_length = None
    response = dict()
    c.put_object(url, token, 'dependency', dep_name, f,
                 content_length, None, None, "application/octet-stream",
                 metadata, None, None, None, response)
    f.close()
    status = response.get('status')
    assert (status == 200 or status == 201)


keystone_url = "http://10.30.223.31:5000/v3"
ACCOUNT = 'vertigo'
USER_NAME = 'vertigo'
PASSWORD = 'vertigo'

url, token = c.get_auth(keystone_url, ACCOUNT + ":"+USER_NAME, PASSWORD, auth_version="3")
# print url, token


"""
 ------------------- Deploy Micro-controllers to Swift Cluster -----------------
"""
path = '../MicrocontrollerSamples'

# No-operation Micro-controller
put_mc_object(url, token, path+'/MC_Noop/bin', 'noop-1.0.jar', 'com.urv.vertigo.mc.noop.NoopHandler')

# Counter Micro-controller
put_mc_object(url, token, path+'/MC_Counter/bin', 'counter-1.0.jar', 'com.urv.vertigo.mc.counter.CounterHandler')

# Prefetching Micro-controller
put_mc_object(url, token, path+'/MC_Prefetching/bin', 'prefetching-1.0.jar', 'com.urv.vertigo.mc.prefetching.PrefetchingHandler')

# CBAC  Micro-controller
put_mc_object(url, token, path+'/MC_CBAC/bin', 'cbac-1.0.jar', 'com.urv.vertigo.mc.cbac.CBACHandler')

# TransGrep  Micro-controller
put_mc_object(url, token, path+'/MC_TransGrep/bin', 'transgrep-1.0.jar', 'com.urv.vertigo.function.transgrep.TransGrepHandler')

print('Done!')
