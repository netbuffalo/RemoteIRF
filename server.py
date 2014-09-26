# -*- coding: utf-8 -*-

import tornado.web
import tornado.ioloop
from tornado.escape import json_encode
from tornado.web import asynchronous

from collections import namedtuple
from collections import OrderedDict
import threading
import time
import signal
import os
import sys
import argparse
import logging
import logging.handlers

here = os.path.abspath(os.path.dirname(__file__))

#logging
logfile = here + "/logs/irserver.log"
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s %(levelname)s - %(message)s')

rfh = logging.handlers.RotatingFileHandler(
    filename=logfile,
    maxBytes=2000000,
    backupCount=3
)
rfh.setLevel(logging.DEBUG)
rfh.setFormatter(formatter)
logger.addHandler(rfh)

stdout = logging.StreamHandler()
stdout.setLevel(logging.DEBUG)
stdout.setFormatter(formatter)
logger.addHandler(stdout)

template_path = os.path.join(here, "templates")
static_path = os.path.join(here, "static")

dev2commands = OrderedDict()
irRequest = None
histries = []
cookie_username = "user"
username = "admin"
password = "password"

is_shutdown = False

def handle_SIGINT(signal, frame) :
   logger.info("shutting down...")
   global is_shutdown
   is_shutdown = True
   sys.exit(0)

class BaseHandler(tornado.web.RequestHandler):
  def get_current_user(self):
    global cookie_username
    return self.get_secure_cookie(cookie_username)
  
class LoginHandler(BaseHandler):
  def initialize(self):
    self.loader = tornado.template.Loader(template_path)

  def get(self):
    try:
      message = self.get_argument("msg")
    except:
      message = ""

    self.write(self.loader.load("login.html").generate(message = message))

  def post(self):
    posted_username = self.get_argument("username")
    posted_password = self.get_argument("password")
    if posted_username == username and posted_password == password:
      self.set_secure_cookie("user", posted_username)
      self.redirect(self.get_argument("next", u"/remocon/top"))
    else:
      logger.warn("login failed. username: %s" % posted_username)
      msg = u"?msg=" + tornado.escape.url_escape("username or password incorrect !")
      self.redirect(u"/remocon/login" + msg)


class SystemHandler(BaseHandler):
  def initialize(self):
    self.loader = tornado.template.Loader(template_path)

  @tornado.web.authenticated
  def get(self, oper = None):
    global histries
    global cookie_username
    if oper and oper == "history":
      logger.info("rendering history page...")
      return self.write(self.loader.load("history.html").generate(histries=histries))
    if oper and oper == "logout":
      logger.info("logged out.")
      self.clear_cookie(cookie_username)
      self.redirect(u"/remocon/login")
    else:
      raise tornado.web.HTTPError(400)

  def post(self, oper = None):
    global irRequest
    logger.info("receiving history...")
    global histries
    if "Content-Type" in self.request.headers and self.request.headers['Content-Type'] == "application/json":
      if oper and oper == "history":
        history = tornado.escape.json_decode(self.request.body)
        if len(histries) >= 50:
          histries.pop(0)
        histries.append(history)
      else:
        raise tornado.web.HTTPError(400)
    else:
      raise tornado.web.HTTPError(400)

class TopHandler(BaseHandler):

  def initialize(self):
    self.loader = tornado.template.Loader(template_path)

  @tornado.web.authenticated
  def get(self):
    self.write(self.loader.load("index.html").generate(devices = dev2commands))


class PutWorker(threading.Thread):
  def __init__(self, callback=None, device=None, command=None, *args, **kwargs):
    super(PutWorker, self).__init__(*args, **kwargs)
    self.callback = callback
    self.device = device
    self.command = command

  def run(self):
    global irRequest
    global is_shutdown
    message = "FAILED"

    logger.info("%s -> %s" % (self.device, self.command))
    if irRequest:
       message = "ALREADY QUEUED."
    else:
      irRequest = {
          'action': 'control',
          'device': self.device,
          'command': self.command
          }
      # wait 
      retries = 300
      for i in range(retries):
        if is_shutdown: break
        time.sleep(0.1)
        if irRequest == None:
          message = "MESSAGE SENT"
          break
        if i == (retries - 1):
          message = "TIMEOUT"
          irRequest = None
          break
    
    logger.info(message)
    # response
    self.callback(message)

class GetWorker(threading.Thread):
  def __init__(self, callback=None, *args, **kwargs):
    super(GetWorker, self).__init__(*args, **kwargs)
    self.callback = callback

  def run(self):
    global irRequest
    global is_shutdown
    response = {
        'action': 'retry',
        'device': '',
        'command': ''
        }

    if irRequest:
      response = irRequest
    else:
      retries = 3000
      for i in range(retries):
        if is_shutdown: break
        time.sleep(0.1)
        if irRequest:
          response = irRequest
          break

    # response
    irRequest = None
    self.callback(response)

class IrRequestHandler(tornado.web.RequestHandler):
  def initialize(self):
    self.loader = tornado.template.Loader(template_path)

  @asynchronous
  def post(self):
    # is ajax request ?
    if "X-Requested-With" in self.request.headers and self.request.headers['X-Requested-With'] == "XMLHttpRequest":
      PutWorker(self.post_callback, self.get_argument("device"), self.get_argument("command")).start()

  def post_callback(self, message):
    self.write(self.loader.load("result.html").generate(msg = message))
    self.finish()

  @asynchronous
  def get(self):
    GetWorker(self.get_callback).start()

  def get_callback(self, response):
    self.write(response)
    self.finish()


class DeviceHandler(tornado.web.RequestHandler):
  def post(self, oper = None):
    global dev2commands
    if "Content-Type" in self.request.headers and self.request.headers['Content-Type'] == "application/json":
      if oper and oper == "init":
        logger.info("initializing device entries...")
        dev2commands.clear()
      elif oper and oper == "add":
        data = tornado.escape.json_decode(self.request.body)
        device = data['device']
        commands = data['commands']
        dev2commands[device] = commands
    else:
      raise tornado.web.HTTPError(400)

    res = { 
      'status': 'OK',
      'msg': ''
    }
    self.write(json_encode(res))


class Application(tornado.web.Application):
  def __init__(self):

    handlers = [
    (r"/remocon/request", IrRequestHandler),
    (r"/remocon/top", TopHandler),
    (r"/remocon/device/?(.*)", DeviceHandler),
    (r"/remocon/login", LoginHandler),
    (r"/remocon/system/?(.*)", SystemHandler)]

    settings = {
        "template_path": template_path,
        "static_path": static_path,
        "cookie_secret": 'gaofjawpoer940r34823842398429afadfi4iias',
        "login_url": '/remocon/login'
      }
    tornado.web.Application.__init__(self, handlers, **settings)

if __name__ == "__main__":
  # parse options
  parser = argparse.ArgumentParser(description='remote IR WebAPI & UI')
  parser.add_argument('-p', '--port', action="store", dest="port", help="port number", default=8888)
  parser.add_argument('-user', '--user', action="store", dest="username", help="login username", default="admin")
  parser.add_argument('-passwd', '--passwd', action="store", dest="password", help="login password", default="password")
  args = parser.parse_args()

  # signal trap
  signal.signal(signal.SIGINT, handle_SIGINT)

  logger.info("starting remote IR WebAPI & UI on %s ..." % args.port)
  username = args.username
  password = args.password
  Application().listen(int(args.port))
  tornado.ioloop.IOLoop.instance().start()
