from vertigo_middleware.handlers import VertigoBaseHandler
from vertigo_middleware.common.utils import verify_access, create_link
from swift.common.swob import HTTPMethodNotAllowed, HTTPNotFound, HTTPUnauthorized, Response
from swift.common.utils import public, cache_from_env
from swift.common.wsgi import make_subrequest
import pickle
import json
import os


class VertigoProxyHandler(VertigoBaseHandler):

    def __init__(self, request, conf, app, logger):
        super(VertigoProxyHandler, self).__init__(
            request, conf, app, logger)

        self.mc_container = self.conf["mc_container"]
        self.memcache = None
        self.request.headers['mc-enabled'] = True
        self.memcache = cache_from_env(self.request.environ)

    def _parse_vaco(self):
        return self.request.split_path(4, 4, rest_with_last=True)

    def _is_object_in_cache(self, obj):
        """
        Checks if an object is in memcache. If exists, the object is stored
        in self.cached_object.
        :return: True/False
        """
        self.logger.info('Vertigo - Checking in cache: ' + obj)
        self.cached_object = self.memcache.get(obj)

        return self.cached_object is not None

    def _get_cached_object(self, obj):
        """
        Gets the object from memcache. Executes associated microcontrollers.
        :return: Response object
        """
        self.logger.info('Vertigo - Object %s in cache', obj)
        cached_obj = pickle.loads(self.cached_object)
        resp_headers = cached_obj["Headers"]
        resp_headers['content-length'] = len(cached_obj["Body"])

        response = Response(body=cached_obj["Body"],
                            headers=resp_headers,
                            request=self.request)

        # TODO: Execute microcontrollers
        return response

    def handle_request(self):
        if hasattr(self, self.request.method) and self.is_valid_request:
            try:
                handler = getattr(self, self.request.method)
                getattr(handler, 'publicly_accessible')
            except AttributeError:
                return HTTPMethodNotAllowed(request=self.request)
            return handler()
        else:
            return self.request.get_response(self.app)
            # return HTTPMethodNotAllowed(request=self.request)

    def _augment_empty_request(self):
        """
        Auxiliary function that sets the content-length header and the body
        of the request in such cases that the user doesn't send the metadata
        file when he assign a microcontroller to an object.
        """
        if 'Content-Length' not in self.request.headers:
            self.request.headers['Content-Length'] = 0
            self.request.body = ''

    def _verify_access(self, cont, obj):
        """
        Verifies access to the specified object in swift
        :param cont: swift container name
        :param obj: swift object name
        :raise HTTPNotFound: if the object doesn't exists in swift
        :return md: Object metadata
        """
        path = os.path.join('/', self.api_version, self.account, cont, obj)
        response = verify_access(self, path)

        if not response.is_success:
            if response.status_int == 401:
                raise HTTPUnauthorized('Unauthorized to access to this '
                                       ' resource: ' + cont + '/' + obj + '\n')
            else:
                raise HTTPNotFound('Vertigo - Object error: "' + cont + '/' +
                                   obj + '" doesn\'t exists in Swift.\n')
        else:
            return response

    def _get_object_list(self, path):
        """
        Gets an object list of a specifiesd path. The path may be '*', that means
        it returns all objects inside the container or a pseudo-folder, that means
        it only returns the objects inside the pseudo-folder.
        :param path: pseudo-folder path (ended with *), or '*'
        :return: list of objects
        """
        obj_list = list()

        dest_path = os.path.join('/', self.api_version, self.account, self.container)
        new_env = dict(self.request.environ)
        auth_token = self.request.headers.get('X-Auth-Token')

        if path == '*':
            # All objects inside a container hierarchy
            obj_list.append('')
        else:
            # All objects inside a pseudo-folder hierarchy
            obj_split = self.obj.rsplit('/', 1)
            pseudo_folder = obj_split[0] + '/'
            new_env['QUERY_STRING'] = 'prefix='+pseudo_folder

        sub_req = make_subrequest(new_env, 'GET', dest_path,
                                  headers={'X-Auth-Token': auth_token},
                                  swift_source='Vertigo')
        response = sub_req.get_response(self.app)
        for obj in response.body.split('\n'):
            if obj != '':
                obj_list.append(obj)

        return obj_list

    def _get_linked_object(self, dest_obj):
        """
        Makes a subrequest to the provided container/object
        :param dest_obj: container/object
        :return: swift.common.swob.Response Instance
        """
        dest_path = os.path.join('/', self.api_version, self.account, dest_obj)
        new_env = dict(self.request.environ)
        sub_req = make_subrequest(new_env, 'GET', dest_path,
                                  headers=self.request.headers,
                                  swift_source='Vertigo')

        return sub_req.get_response(self.app)

    def _get_pseudo_folder_metadata(self, psudo_folder):
        """
        Makes a HEAD to the specified pseudo-folder (7ms overhead)
        :param dest_obj: container/object
        :return: swift.common.swob.Response Instance
        """
        dest_path = os.path.join('/', self.api_version, self.account, self.container, psudo_folder)
        new_env = dict(self.request.environ)
        auth_token = self.request.headers.get('X-Auth-Token')
        sub_req = make_subrequest(new_env, 'HEAD', dest_path,
                                  headers={'X-Auth-Token': auth_token},
                                  swift_source='Vertigo')
        response = sub_req.get_response(self.app)
        return response.headers

    def _process_trigger_assignation_deletion_request(self):
        """
        Process both trigger assignation and trigger deletion ovwer an object
        or a group of objects
        """
        self.request.method = 'PUT'
        obj_list = list()
        if self.is_trigger_assignation:
            _, micro_controller = self.get_mc_assignation_data()
            self._verify_access(self.mc_container, micro_controller)

        if '*' in self.obj:
            obj_list = self._get_object_list(self.obj)
        else:
            obj_list.append(self.obj)

        specific_md = self.request.body

        for obj in obj_list:
            self.request.body = specific_md
            response = self._verify_access(self.container, obj)
            new_path = os.path.join('/', self.api_version, self.account, self.container, obj)
            if response.headers['Content-Type'] == 'vertigo/link':
                link = response.headers["X-Object-Sysmeta-Vertigo-Link-to"]
                container, obj = link.split('/', 2)
                self._verify_access(container, obj)
                new_path = os.path.join('/', self.api_version, self.account, container, obj)
            self.request.environ['PATH_INFO'] = new_path
            self._augment_empty_request()

            response = self.request.get_response(self.app)

            print response.body

        return response

    def _check_microcntroller_execution(self, obj):
        user_agent = self.request.headers['User-Agent']
        if user_agent == "vertigo/microcontroller":
            req_token = self.request.headers['X-Vertigo-Token']
            key = 'VERTIGO_TOKEN_' + req_token.split('-')[0] + "_" + obj
            admin_token = self.memcache.get(key)
            req_token = self.request.headers['X-Vertigo-Token']
            if req_token == admin_token:
                self.request.headers['mc-enabled'] = False
                self.logger.info('Vertigo - Microcontroller execution disabled'
                                 ': Request from microcontroller')

    @public
    def GET(self):
        """
        GET handler on Proxy
        """
        obj = os.path.join(self.account, self.container, self.obj)
        self._check_microcntroller_execution(obj)

        if self._is_object_in_cache(obj):
            response = self._get_cached_object(obj)
        else:
            response = self.request.get_response(self.app)

        if response.headers['Content-Type'] == 'vertigo/link':
            dest_obj = response.headers['X-Object-Sysmeta-Vertigo-Link-to']
            obj = os.path.join(self.account, dest_obj)
            if self._is_object_in_cache(obj):
                response = self._get_cached_object(obj)
            else:
                response = self._get_linked_object(dest_obj)

        if 'Storlet-List' in response.headers and \
                self.is_account_storlet_enabled():
            self.logger.info('Vertigo - There are Storlets to execute')
            storlet_list = json.loads(response.headers.pop('Storlet-List'))
            response = self.apply_storlet_on_get(response, storlet_list)

        if 'Content-Length' not in response.headers:
            response.headers['Content-Length'] = None
            if 'Transfer-Encoding' in response.headers:
                response.headers.pop('Transfer-Encoding')

        return response

    @public
    def PUT(self):
        """
        PUT handler on Proxy
        """
        if self.is_trigger_assignation or self.is_trigger_deletion:
            response = self._process_trigger_assignation_deletion_request()

        elif self.is_object_grouping:
            pass

        elif self.is_object_move:
            link_path = os.path.join(self.container, self.obj)
            dest_path = self.request.headers['X-Vertigo-Link-To']
            if link_path != dest_path:
                response = self._verify_access(self.container, self.obj)
                headers = response.headers
                if "X-Object-Sysmeta-Vertigo-Link-to" not in response.headers \
                        and response.headers['Content-Type'] != 'vertigo/link':
                    self.request.method = 'COPY'
                    self.request.headers['Destination'] = dest_path
                    response = self.request.get_response(self.app)
                if response.is_success:
                    response = create_link(self, link_path, dest_path, headers)
            else:
                msg = ("Vertigo - Error: Link path and destination path "
                       "cannot be the same.\n")
                response = Response(body=msg, headers={'etag': ''},
                                    request=self.request)
        else:
            response = self.request.get_response(self.app)

        return response

    @public
    def POST(self):
        """
        POST handler on Proxy
        """
        if self.is_trigger_assignation or self.is_trigger_deletion:
            response = self._process_trigger_assignation_deletion_request()
        else:
            response = self.request.get_response(self.app)

        return response

    @public
    def HEAD(self):
        """
        HEAD handler on Proxy
        """
        response = self.request.get_response(self.app)

        for key in response.headers.keys():
            if key.startswith('X-Object-Sysmeta-Vertigo-'):
                new_key = key.replace('X-Object-Sysmeta-', '')
                response.headers[new_key] = response.headers[key]

        if 'Vertigo-Microcontroller' in response.headers:
            mc_dict = eval(response.headers['Vertigo-Microcontroller'])
            for trigger in mc_dict.keys():
                if not mc_dict[trigger]:
                    del mc_dict[trigger]
            response.headers['Vertigo-Microcontroller'] = mc_dict

        return response

    @public
    def MOVE(self):
        """
        MOVE handler on Proxy
        """
        link_path = os.path.join(self.container, self.obj)
        dest_path = self.request.headers['Destination']

        if link_path != dest_path:
            response = self._verify_access(self.container, self.obj)
            if "X-Object-Sysmeta-Vertigo-Link-to" not in response.headers \
                    and response.headers['Content-Type'] != 'vertigo/link':
                self.request.method = 'COPY'
                response = self.request.get_response(self.app)
            if response.is_success:
                response = create_link(self, link_path, dest_path)
        else:
            msg = ("Vertigo - Error: Link path and destination path"
                   "are the same.\n")
            response = Response(body=msg, headers={'etag': ''},
                                request=self.request)
        return response
