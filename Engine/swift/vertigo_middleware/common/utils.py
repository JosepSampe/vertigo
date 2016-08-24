from swift.common.internal_client import InternalClient
from swift.common.exceptions import DiskFileXattrNotSupported, DiskFileNoSpace, DiskFileNotExist
from swift.obj.diskfile import get_data_dir as df_data_dir, get_ondisk_files, _get_filename
from swift.common.request_helpers import get_name_and_placement
from swift.common.utils import storage_directory, hash_path
from swift.common.wsgi import make_subrequest
import xattr
import logging
import pickle
import errno
import os


PICKLE_PROTOCOL = 2
VERTIGO_METADATA_KEY = 'user.swift.microcontroller'
SYSMETA_HEADER = 'X-Object-Sysmeta-Vertigo-'
VERTIGO_MC_HEADER = SYSMETA_HEADER+'Microcontroller'
SWIFT_METADATA_KEY = 'user.swift.metadata'
LOCAL_PROXY = '/etc/swift/storlet-proxy-server.conf'
DEFAULT_MD_STRING = {'onget': None,
                     'onput': None,
                     'ondelete': None,
                     'ontimer': None}


def read_metadata(fd, md_key=None):
    """
    Helper function to read the pickled metadata from an object file.
    
    :param fd: file descriptor or filename to load the metadata from
    :param md_key: metadata key to be read from object file
    :returns: dictionary of metadata
    """
    if md_key:
        meta_key = md_key
    else:
        meta_key = VERTIGO_METADATA_KEY

    metadata = ''
    key = 0
    try:
        while True:
            metadata += xattr.getxattr(fd, '%s%s' % (meta_key,
                                                     (key or '')))
            key += 1
    except (IOError, OSError) as e:
        if metadata == '':
            return False
        for err in 'ENOTSUP', 'EOPNOTSUPP':
            if hasattr(errno, err) and e.errno == getattr(errno, err):
                msg = "Filesystem at %s does not support xattr" % \
                      _get_filename(fd)
                logging.exception(msg)
                raise DiskFileXattrNotSupported(e)
        if e.errno == errno.ENOENT:
            raise DiskFileNotExist()
    return pickle.loads(metadata)


def write_metadata(fd, metadata, xattr_size=65536, md_key=None):
    """
    Helper function to write pickled metadata for an object file.

    :param fd: file descriptor or filename to write the metadata
    :param md_key: metadata key to be write to object file
    :param metadata: metadata to write
    """
    if md_key:
        meta_key = md_key
    else:
        meta_key = VERTIGO_METADATA_KEY

    metastr = pickle.dumps(metadata, PICKLE_PROTOCOL)
    key = 0
    while metastr:
        try:
            xattr.setxattr(fd, '%s%s' % (meta_key, key or ''),
                           metastr[:xattr_size])
            metastr = metastr[xattr_size:]
            key += 1
        except IOError as e:
            for err in 'ENOTSUP', 'EOPNOTSUPP':
                if hasattr(errno, err) and e.errno == getattr(errno, err):
                    msg = "Filesystem at %s does not support xattr" % \
                          _get_filename(fd)
                    logging.exception(msg)
                    raise DiskFileXattrNotSupported(e)
            if e.errno in (errno.ENOSPC, errno.EDQUOT):
                msg = "No space left on device for %s" % _get_filename(fd)
                logging.exception(msg)
                raise DiskFileNoSpace()
            raise


def get_swift_metadata(data_file):
    """
    Retrieves the swift metadata of a specified data file
    
    :param data_file: full path of the data file
    :returns: dictionary with all swift metadata
    """
    fd = open_data_file(data_file)
    metadata = read_metadata(fd, SWIFT_METADATA_KEY)
    close_data_file(fd)
    
    return metadata


def set_swift_metadata(data_file, metadata):
    """
    Sets the swift metadata to the specified data file
    
    :param data_file: full path of the data file
    """
    fd = open_data_file(data_file)
    write_metadata(fd, metadata, md_key = SWIFT_METADATA_KEY)
    close_data_file(fd)


def make_swift_request(op, account, container=None, obj=None):
    """
    Makes a swift request via a local proxy (cost expensive)
    
    :param op: opertation (PUT, GET, DELETE, HEAD)
    :param account: swift account
    :param container: swift container
    :param obj: swift object
    :returns: swift.common.swob.Response instance
    """ 
    iclient = InternalClient(LOCAL_PROXY, 'SA', 1)
    path = iclient.make_path(account, container, obj)
    resp = iclient.make_request(op, path, {'PATH_INFO': path}, [200])
    
    return resp


def verify_access(vertigo, path):
    """
    Verifies access to the specified object in swift
    
    :param vertigo: swift_vertigo.vertigo_handler.VertigoProxyHandler instance
    :param path: swift path of the object to check
    :returns: headers of the object whether exists
    """ 
    vertigo.logger.debug('Vertigo - Verify access to %s' % path)
    
    new_env = dict(vertigo.request.environ)
    if 'HTTP_TRANSFER_ENCODING' in new_env.keys():
        del new_env['HTTP_TRANSFER_ENCODING']   
    
    for key in DEFAULT_MD_STRING.keys():
        env_key = 'HTTP_X_VERTIGO_' + key.upper()
        if env_key in new_env.keys():
            del new_env[env_key]

    auth_token = vertigo.request.headers.get('X-Auth-Token')
    sub_req = make_subrequest(
        new_env, 'HEAD', path,
        headers={'X-Auth-Token': auth_token},
        swift_source='Vertigo')

    resp = sub_req.get_response(vertigo.app)
    
    if not resp.is_success:
        return False

    return resp.headers

def create_link(vertigo, link_path, dest_path):              
    """
    Creates a link to a real object
    
    :param vertigo: swift_vertigo.vertigo_handler.VertigoProxyHandler instance
    :param link_path: swift path of the link
    :param dest_path: swift path of the object to link
    """   
    vertigo.logger.debug('Vertigo - Creating link %s to %s' % (link_path, 
                                                               dest_path))
    
    new_env = dict(vertigo.request.environ)
    if 'HTTP_TRANSFER_ENCODING' in new_env.keys():
        del new_env['HTTP_TRANSFER_ENCODING']
    
    if 'HTTP_X_COPY_FROM' in new_env.keys():
        del new_env['HTTP_X_COPY_FROM']

    auth_token = vertigo.request.headers.get('X-Auth-Token')
    
    sub_req = make_subrequest(
            new_env, 'PUT', link_path,
            headers={'X-Auth-Token': auth_token, 
                     'Content-Length':0, 
                     'Content-Type':'Vertigo-Link',
                     'X-Object-Sysmeta-Vertigo-Link-to':dest_path},
            swift_source='Vertigo')
    resp = sub_req.get_response(vertigo.app)

    return resp


def get_data_dir(vertigo):
    """
    Gets the data directory full path
    
    :param vertigo: swift_vertigo.vertigo_handler.VertigoObjectHandler instance
    :returns: the data directory path
    """ 
    devices = vertigo.conf.get('devices')
    device, partition, account, container, obj, policy = \
        get_name_and_placement(vertigo.request, 5, 5, True)
    name_hash = hash_path(account, container, obj)
    device_path = os.path.join(devices, device)
    storage_dir = storage_directory(df_data_dir(policy),partition, name_hash)
    data_dir = os.path.join(device_path, storage_dir)
    
    return data_dir


def get_data_file(vertigo):
    """
    Gets the data file full path
    
    :param vertigo: swift_vertigo.vertigo_handler.VertigoObjectHandler instance
    :returns: the data file path
    """
    data_dir = get_data_dir(vertigo)
    files = os.listdir(data_dir)
    data_file, meta_file, ts_file = get_ondisk_files(files, data_dir)

    return data_file


def open_data_file(data_file):
    """
    Open a data file
    
    :param data_file: full path of the data file
    :returns: a file descriptor of the open data file
    """ 
    fd = os.open(data_file, os.O_RDONLY)
    return fd


def close_data_file(fd):
    """
    Close a file descriptor
    
    :param fd: file descriptor
    """ 
    os.close(fd) 


def set_microcontroller(vertigo, trigger, mc):
    """
    Sets a microcontroller to the specified object in the main request,
    and stores the metadata file
    
    :param vertigo: swift_vertigo.vertigo_handler.VertigoObjectHandler instance
    :param trigger: trigger name
    :param mc: microcontroller name
    :raises HTTPInternalServerError: If it fails
    """
    
    # 1st: set microcontroller name to list
    try:
        mc_dict = get_microcontroller_dict(vertigo)
    except:
        raise ValueError('Vertigo - ERROR: There was an error getting trigger'
                         ' dictionary from the object.\n')

    if not mc_dict:
        mc_dict = DEFAULT_MD_STRING
    if not mc_dict[trigger]:
        mc_dict[trigger] = list()      
    if not mc in mc_dict[trigger]:
        mc_dict[trigger].append(mc)

    # 2nd: Set microcontroller specific metadata
    specific_md = vertigo.request.body.rstrip()

    # 3rd: Assign all metadata to the object
    try:        
        data_file = get_data_file(vertigo)  
        metadata = get_swift_metadata(data_file)
        metadata[VERTIGO_MC_HEADER] = mc_dict
            
        sysmeta_key = SYSMETA_HEADER+trigger+'-'+mc
        if specific_md:
            metadata[sysmeta_key] = specific_md
        else:
            if SYSMETA_HEADER+trigger+'-'+mc in metadata:
                del metadata[sysmeta_key]
        
        set_swift_metadata(data_file, metadata)  
    except:
        raise ValueError('Vertigo - ERROR: There was an error setting trigger'
                         ' dictionary from the object.\n')


def clean_microcontroller_dict(metadata):
    """
    Auxiliary function that cleans the microcontroller dictionary, deleting
    empty lists for each trigger, and deleting all dictionary whether all
    values are None.
    
    :param microcontroller_dict: microcontroller dictionary
    :returns microcontroller_dict: microcontroller dictionary
    """   
    for trigger in metadata[VERTIGO_MC_HEADER].keys():
        if not metadata[VERTIGO_MC_HEADER][trigger]:
            metadata[VERTIGO_MC_HEADER][trigger] = None
            
    if all(value == None for value in metadata[VERTIGO_MC_HEADER].values()):
        del metadata[VERTIGO_MC_HEADER]
    
    return  metadata            


def delete_microcontroller(vertigo, trigger, mc):
    """
    Deletes a microcontroller to the specified object in the main request
    
    :param vertigo: swift_vertigo.vertigo_handler.VertigoObjectHandler instance
    :param trigger: trigger name
    :param mc: microcontroller name
    :raises HTTPInternalServerError: If it fails
    """        
    vertigo.logger.debug('Vertigo - Go to delete "' + mc +
                         '" microcontroller from "' + trigger + '" trigger')
            
    try:
        data_file = get_data_file(vertigo)  
        metadata = get_swift_metadata(data_file)
    except:
        raise ValueError('Vertigo - ERROR: There was an error getting trigger' 
                         ' metadata from the object.\n')

    try:
        if trigger == "vertigo" and mc == "all":   
            for key in metadata.keys():
                if key.startswith(SYSMETA_HEADER):
                    del metadata[key]
        else:
            if metadata[VERTIGO_MC_HEADER]:  
                if mc == 'all':
                    mc_list = metadata[VERTIGO_MC_HEADER][trigger]
                    metadata[VERTIGO_MC_HEADER][trigger] = None
                    for mc_k in mc_list:
                        sysmeta_key = SYSMETA_HEADER+trigger+'-'+mc_k
                        if sysmeta_key in metadata:
                            del metadata[sysmeta_key]
                elif mc in metadata[VERTIGO_MC_HEADER][trigger]:
                    metadata[VERTIGO_MC_HEADER][trigger].remove(mc)
                    sysmeta_key = SYSMETA_HEADER+trigger+'-'+mc
                    if sysmeta_key in metadata:
                            del metadata[sysmeta_key] 
                else:
                    raise
                metadata = clean_microcontroller_dict(metadata)
            else:
                raise
        set_swift_metadata(data_file, metadata)
    except:
        raise ValueError('Vertigo - Error: Microcontroller "'+mc+'" not'
                         ' assigned to the "' + trigger + '" trigger.\n')      
 
    data_dir = get_data_dir(vertigo)
    vertigo.logger.debug('Vertigo - Object path: ' + data_dir)


def get_microcontroller_dict(vertigo):
    """
    Gets the list of associated microcontrollers to the requested object.
    This method retrieves a dictionary with all triggers and all 
    microcontrollers associated to each trigger.
    
    :param vertigo: swift_vertigo.vertigo_handler.VertigoObjectHandler instance
    :returns: microcontroller dictionary
    """
    data_file = get_data_file(vertigo)
    metadata = get_swift_metadata(data_file)

    if VERTIGO_MC_HEADER in metadata:
        if isinstance(metadata[VERTIGO_MC_HEADER], dict):
            return metadata[VERTIGO_MC_HEADER]
        else:
            return eval(metadata[VERTIGO_MC_HEADER])
    else:
        return None


def get_microcontroller_list(headers, method):
    """
    Gets the list of associated microcontrollers to the requested object.
    This method gets the microcontroller dictionary from the object headers,
    and filter the content to return only a list of names of microcontrollers 
    associated to the type of request (put, get, delete)
    
    :param headers: response headers of the object
    :param method: current method
    :returns: microcontroller list associated to the type of the request
    """
    if headers[VERTIGO_MC_HEADER]:
        microcontroller_dict = eval(headers[VERTIGO_MC_HEADER])
        mc_list = microcontroller_dict["on" + method]
    else:
        mc_list = None
    
    return mc_list
