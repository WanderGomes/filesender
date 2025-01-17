#!/usr/bin/env python3
#
# FileSender www.filesender.org
#
# Copyright (c) 2009-2019, AARNet, Belnet, HEAnet, SURFnet, UNINETT
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# *   Redistributions of source code must retain the above copyright
#     notice, this list of conditions and the following disclaimer.
# *   Redistributions in binary form must reproduce the above copyright
#     notice, this list of conditions and the following disclaimer in the
#     documentation and/or other materials provided with the distribution.
# *   Neither the name of AARNet, Belnet, HEAnet, SURFnet and UNINETT nor the
#     names of its contributors may be used to endorse or promote products
#     derived from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import argparse
try:
  import textwrap #used to format help description and epilog
  import requests
  import time
  import re
  from collections.abc import Iterable
  from collections.abc import MutableMapping
  import hmac
  import concurrent.futures
  import hashlib
  import urllib3
  import os
  import sys
  import json
  import configparser
  from os.path import expanduser
except Exception as e:
  print(type(e))
  print(e.args)
  print(e)
  print('')
  print('ERROR: A required dependency is not installed, please check your')
  print('distribution packages or run something like the following')
  print('')
  print('pip3 install requests urllib3 ')
  exit(1)
  

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

#settings
base_url = '[base_url]'
default_transfer_days_valid = 10
username = None
apikey = None
homepath = expanduser("~")

config = configparser.ConfigParser()
config.read(homepath + '/.filesender/filesender.py.ini')
if 'system' in config:
  base_url = config['system'].get('base_url', '[base_url]')
  default_transfer_days_valid = int(config['system'].get('default_transfer_days_valid', 10))
if 'user' in config:
  username = config['user'].get('username')
  apikey = config['user'].get('apikey')



  


#argv
parser = argparse.ArgumentParser(
    formatter_class=argparse.RawDescriptionHelpFormatter,
    description=textwrap.dedent(f'''\
      File Sender CLI client.
      Source code: https://github.com/filesender/filesender/blob/master/scripts/client/filesender.py
    '''),
    epilog=textwrap.dedent(f'''\
      A config file can be added to {homepath}/.filesender/filesender.py.ini to avoid having to specify username and apikey on the command line.

      Example (Config file is present): 
      python filesender.py -r reciever@example.com file1.txt''')
)
parser.add_argument("files", help="path to file(s) to send", nargs='+')
parser.add_argument("-v", "--verbose", action="store_true")
parser.add_argument("-i", "--insecure", action="store_true")
parser.add_argument("-p", "--progress", action="store_true")
parser.add_argument("-s", "--subject")
parser.add_argument("-m", "--message")
parser.add_argument("-g", "--guest", action="store_true")
parser.add_argument("--threads")
parser.add_argument("--timeout")
parser.add_argument("--retries")


requiredNamed = parser.add_argument_group('required named arguments')

# if we have found these in the config file they become optional arguments
if username is None:
  requiredNamed.add_argument("-u", "--username", required=True)
else:
  parser.add_argument("-u", "--username")
  
if apikey is None:
  requiredNamed.add_argument("-a", "--apikey", required=True)
else:
  parser.add_argument("-a", "--apikey")
  
requiredNamed.add_argument("-r", "--recipients", required=True)
args = parser.parse_args()
debug = args.verbose
progress = args.progress
insecure = args.insecure
guest = args.guest
user_threads = args.threads
user_timeout = args.timeout
user_retries = args.retries
if args.username is not None:
  username = args.username
  
if args.apikey is not None:
  apikey = args.apikey


#configs
try:
  info_response = requests.get(base_url+'/info', verify=True)
  config_response = requests.get(base_url[0:-9]+'/filesender-config.js.php',verify=True)#for terasender config not in info.
except requests.exceptions.SSLError as exc:
  if not insecure:
    print('Error: the SSL certificate of the server you are connecting to cannot be verified:')
    print(exc)
    print('For more information, please refer to https://www.digicert.com/ssl/. If you are absolutely certain of the identity of the server you are connecting to, you can use the --insecure flag to bypass this warning. Exiting...')
    sys.exit(1)
  elif insecure:
    print('Warning: Error: the SSL certificate of the server you are connecting to cannot be verified:')
    print(exc)
    print('Running with --insecure flag, ignoring warning...')
    info_response = requests.get(base_url+'/info', verify=False)
    config_response = requests.get(base_url[-9]+'/filesender-config.js.php',verify=False)

upload_chunk_size = info_response.json()['upload_chunk_size']

try:
    regex_match = re.search(r"terasender_worker_count\D*(\d+)",config_response.text)
    worker_count =  int(regex_match.group(1))
    regex_match = re.search(r"terasender_worker_start_must_complete_within_ms\D*(\d+)",config_response.text)
    worker_timeout = int(regex_match.group(1)) // 1000
    regex_match = re.search(r"terasender_worker_max_chunk_retries\D*(\d+)",config_response.text)
    worker_retries = int(regex_match.group(1))
    regex_match = re.search(r"terasender_enabled\W*(\w+)",config_response.text)
    terasender_enabled = regex_match.group(1) == "true"
except Exception as e:
    print("Failed to parse match")
    print(e)
    worker_count = 4
    worker_timeout = 180
    max_chunk_retries = 20
    terasender_enabled = False

if terasender_enabled:
  if user_threads:
    worker_count = min(int(user_threads), worker_count)
else:
  worker_count = 1

if user_timeout:
  worker_timeout = min(int(user_timeout), worker_timeout)
if user_retries:
  worker_retries  = min(int(user_retries), worker_retries)


if debug:
  print('base_url          : '+base_url)
  print('username          : '+username)
  print('apikey            : '+apikey)
  print('upload_chunk_size : '+str(upload_chunk_size)+' bytes')
  print('recipients        : '+args.recipients)
  print('files             : '+','.join(args.files))
  print('insecure          : '+str(insecure))


##########################################################################

def flatten(d, parent_key=''):
  items = []
  for k, v in d.items():
    new_key = parent_key + '[' + k + ']' if parent_key else k
    if isinstance(v, MutableMapping):
      items.extend(flatten(v, new_key).items())
    else:
      items.append(new_key+'='+v)
  items.sort()
  return items

def call(method, path, data, content=None, rawContent=None, options={}, tryCount=0):
  initData = {}
  for k in data:
    initData[k] = data[k]
  data['remote_user'] = username
  data['timestamp'] = str(round(time.time()))
  flatdata=flatten(data)
  signed = bytes(method+'&'+base_url.replace('https://','',1).replace('http://','',1)+path+'?'+('&'.join(flatten(data))), 'ascii')

  content_type = options['Content-Type'] if 'Content-Type' in options else 'application/json'

  inputcontent = None
  if content is not None and content_type == 'application/json':
    inputcontent = json.dumps(content,separators=(',', ':'))
    signed += bytes('&'+inputcontent, 'ascii')
  elif rawContent is not None:
    inputcontent = rawContent
    signed += bytes('&', 'ascii')
    signed += inputcontent

  #print(signed)
  bkey = bytearray()
  bkey.extend(map(ord, apikey))
  data['signature'] = hmac.new(bkey, signed, hashlib.sha1).hexdigest()

  url = base_url+path+'?'+('&'.join(flatten(data)))
  headers = {
    "Accept": "application/json",
    "Content-Type": content_type
  }
  response = None
  try:
    if method == "get":
      response = requests.get(url, verify=not insecure, headers=headers, timeout=worker_timeout)
    elif method == "post":
      response = requests.post(url, data=inputcontent, verify=not insecure, headers=headers, timeout=worker_timeout)
    elif method == "put":
      response = requests.put(url, data=inputcontent, verify=not insecure, headers=headers, timeout=worker_timeout)
    elif method == "delete":
      response = requests.delete(url, verify=not insecure, headers=headers, timeout=worker_timeout)
  except Exception as _exc:
    if progress or debug:
      print("Failure when attempting to call: " + url)
      print("Retry attempt " + str((tryCount + 1)))
    if debug:
      print(_exc)
    if tryCount < worker_retries:
      time.sleep(300)
      return call(method=method, path=path, data=initData,
                  content=content, rawContent=rawContent,
                   options=options, tryCount=tryCount + 1)

    raise _exc
  if response is None:
    raise Exception('Client error')

  code = response.status_code
  #print(url)
  #print(inputcontent)
  #print(code)
  #print(response.text)

  if code!=200:
    if method!='post' or code!=201:
      if tryCount > worker_retries:
        raise Exception('Http error '+str(code)+' '+response.text)
      else:
        if progress or debug:
          print("Failure when attempting to call: " + url)
          print("Retry attempt " + str((tryCount + 1)))
        if debug:
          print("Fail Reason: " + str(code))
          print(response.text)          
        time.sleep(300)
        return call(method=method, path=path, data=initData,
                  content=content, rawContent=rawContent,
                   options=options, tryCount=tryCount + 1)

  if response.text=="":
    raise Exception('Http error '+str(code)+' Empty response')

  if method!='post':
    return response.json()

  r = {}
  r['location']=response.headers['Location']
  r['created']=response.json()
  return r

def postTransfer(user_id, files, recipients, subject=None, message=None, expires=None, options=[]):

  if expires is None:
    expires = round(time.time()) + (default_transfer_days_valid*24*3600)

  to = [x.strip() for x in recipients.split(',')]
  
  return call(
    'post',
    '/transfer',
    {},
    {
      'from': user_id,
      'files': files,
      'recipients': to,
      'subject': subject,
      'message': message,
      'expires': expires,
      'aup_checked':1,
      'options': options
    },
    None,
    {}
  )

def putChunk(t, f, chunk, offset):
  return call(
    'put',
    '/file/'+str(f['id'])+'/chunk/'+str(offset),
    { 'key': f['uid'], 'roundtriptoken': t['roundtriptoken'] },
    None,
    chunk,
    { 'Content-Type': 'application/octet-stream' }
  )

def fileComplete(t,f):
  return call(
    'put',
    '/file/'+str(f['id']),
    { 'key': f['uid'], 'roundtriptoken': t['roundtriptoken'] },
    { 'complete': True },
    None,
    {}
  )

def transferComplete(transfer):
  return call(
    'put',
    '/transfer/'+str(transfer['id']),
    { 'key': transfer['files'][0]['uid'] },
    { 'complete': True },
    None,
    {}
  )

def deleteTransfer(transfer):
  return call(
    'delete',
    '/transfer/'+str(transfer['id']),
    { 'key': transfer['files'][0]['uid'] },
    None,
    None,
    {}
  )


def postGuest(user_id, recipient, subject=None, message=None, expires=None, options=[]):

  if expires is None:
    expires = round(time.time()) + (default_transfer_days_valid*24*3600)

  return call(
    'post',
    '/guest',
    {},
    {
      'from': user_id,
      'recipient': recipient,
      'subject': subject,
      'message': message,
      'expires': expires,
      'aup_checked':1,
      'options': options
    },
    None,
    {}
  )

##########################################################################

#postTransfer
if debug:
  print('postTransfer')

if guest:
  print('creating new guest ' + args.recipients)
  troptions = {'get_a_link':0}
  r = postGuest( username,
                 args.recipients,
                 subject=args.subject,
                 message=args.message,
                 expires=None,
                 options=troptions)
  exit(0)

files = {}
filesTransfer = []
for f in args.files:
  fn_abs = os.path.abspath(f)
  fn = os.path.basename(fn_abs)
  size = os.path.getsize(fn_abs)

  files[fn+':'+str(size)] = {
    'name':fn,
    'size':size,
    'path':fn_abs
  }
  filesTransfer.append({'name':fn,'size':size})

troptions = {'get_a_link':0}


transfer = postTransfer( username,
                         filesTransfer,
                         args.recipients,
                         subject=args.subject,
                         message=args.message,
                         expires=None,
                         options=troptions)['created']
#print(transfer)

try:
  for f in transfer['files']:
    path = files[f['name']+':'+str(f['size'])]['path']
    size = files[f['name']+':'+str(f['size'])]['size']
    #putChunks
    if debug:
      print('putChunks: '+path)
    with open(path, mode='rb', buffering=0) as fin:
      progressed_cunks = 0
      with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as e:
        fut = [e.submit((lambda x:putChunk(transfer, f, fin.read(upload_chunk_size), x)), i) for i in range(0,size,upload_chunk_size)]
        for r in concurrent.futures.as_completed(fut):
          if progress:
            progressed_cunks += upload_chunk_size
            print('Uploading: '+path+' '+' '+str(min(round(progressed_cunks/size*100),100))+'%')

    #fileComplete
    if debug:
      print('fileComplete: '+path)
    fileComplete(transfer,f)
    if progress:
      print('Uploading: '+path+' '+str(size)+' 100%')


  #transferComplete
  if debug:
    print('transferComplete')
  transferComplete(transfer)
  if progress:
    print('Upload Complete')

except Exception as inst:
  print(type(inst))
  print(inst.args)
  print(inst)

  #deleteTransfer
  if debug:
    print('deleteTransfer')
  deleteTransfer(transfer)
