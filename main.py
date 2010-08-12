#!/usr/bin/env python

from google.appengine.ext import webapp
from google.appengine.ext.webapp import util
from google.appengine.api import memcache
from google.appengine.api import urlfetch 
from google.appengine.api import images
from google.appengine.ext import db
from datetime import datetime, timedelta
import urllib
import cgi

# investigate python_precompiled
# Pure Python PNG parser -- http://packages.python.org/pypng/png.html
# Motion detection -- http://www.codeproject.com/KB/audio-video/Motion_Detection.aspx
# PIL http://www.pythonware.com/library/pil/handbook/introduction.htm
# http://code.google.com/apis/youtube/1.0/developers_guide_python.html#DirectUpload

# ----------------------------------------------------------------------

class CameraSource(db.Model):
    title = db.StringProperty()
    url = db.LinkProperty()
    # username/password auth
    poll_max_fps = db.IntegerProperty()
    alert_max_fps = db.IntegerProperty()
    creation_time = db.DateTimeProperty(auto_now_add=True)
    last_edited = db.DateTimeProperty()
    enabled = db.BooleanProperty()
    deleted = db.BooleanProperty()
    # mode?  modetect, record
    # frames before/after event
    # sensitivity


class CameraEvent(db.Model):
    camera_id = db.ReferenceProperty(CameraSource)
    event_start = db.DateTimeProperty()
    event_end = db.DateTimeProperty()
    motion_rating = db.RatingProperty()
    comments = db.StringProperty()
    category = db.CategoryProperty()
    archived = db.BooleanProperty()
    deleted = db.BooleanProperty()
    viewed = db.BooleanProperty()


class CameraFrame(db.Model):
    camera_id = db.ReferenceProperty(CameraSource)
    event_id = db.ReferenceProperty(CameraEvent)
    image_time = db.DateTimeProperty()
    full_size_image = db.BlobProperty()



MAX_CAMERAS = 10

# ----------------------------------------------------------------------

# This is the background handler that gets invoked to poll images for motion.
class ImageFetcherTask(webapp.RequestHandler):
    def get(self):
        q = CameraSource.all()
        q.filter("enabled =",True).filter("deleted =",False)

        results = q.fetch(MAX_CAMERAS)
        # TODO: needs to keep looping for about a minute, honoring the poll_max_fps
        for cam in results:
            response = urlfetch.fetch(cam.url, payload=None, method=urlfetch.GET, headers={}, allow_truncated=False, follow_redirects=True, deadline=5)

            # TODO: check status code and content-type, handle exceptions
            #if response.status_code != 200:

            # store in memcache
            memcache.set("camera{%s}" % cam.key(), response.content)

            # store in the database
            s2 = CameraFrame()
            s2.camera_id = cam.key()
            s2.full_size_image = response.content
            s2.image_time = datetime.now()
            s2.put()

            # pre-process for motion detection
            #img = images.Image(image_data=imgdata)
            #img.resize(width=100, height=100)
            #img.im_feeling_lucky()
            #thumbnail = img.execute_transforms(output_encoding=images.PNG)

        self.response.headers['Content-Type'] = 'text/html'
        self.response.out.write("done")



class AddCameraSourceHandler(webapp.RequestHandler):
    def get(self):
        cam = CameraSource()
        cam.title = "My Camera"
        #cam.url = "http://cowpad.dyn-o-saur.com:8080/xxxxx"
        cam.url = "http://www.bovine.net/~jlawson/webcam/homecam.jpg"
        cam.poll_max_fps = 1
        cam.alert_max_fps = 10
        cam.creation_time = datetime.now()
        cam.enabled = True
        cam.deleted = False
        cam.put()

        self.response.out.write("added")


class EditCameraSourceHandler(webapp.RequestHandler):
    def get(self):
        self.response.out.write("unimplemented")


class DeleteCameraSourceHandler(webapp.RequestHandler):
    def get(self):
        keyname = self.request.get('camera')
        if keyname is not None:
            cam = CameraSource.get(db.Key(keyname))
            cam.deleted = True
            cam.put()

        self.response.out.write("deleted")


class GarbageCollectorTask(webapp.RequestHandler):
    def get(self):
        # Look for CameraSources marked deleted
        # Look for Cameraevents marked deleted
        self.response.out.write("unimplemented")
        


# Display a HTML status table summarizing all cameras currently in the system and the number of recent events.
class MainSummaryHandler(webapp.RequestHandler):
    def get(self):
        q = CameraSource.all()
        q.filter("deleted =", False).order("-creation_time")

        results = q.fetch(MAX_CAMERAS)

        #self.response.headers['Content-Type'] = 'text/html; charset=utf-8'
        #self.response.out.write("<script src='http://ajax.googleapis.com/ajax/libs/jquery/1.4.2/jquery.min.js' type='text/javascript'></script>");
        self.response.out.write("<table border=1>")
        self.response.out.write("<tr><th>Title</th><th>Url</th><th>Thumbnail</th><th>Total</th><th>Today</th><th>Yesterday</th><th>This Week</th><th>This Month</th><th>Actions</th></tr>")
        for cam in results:
            #cgi.escape
            #urllib.quote_plus

            q2 = CameraFrame.all()
            q2.filter("camera_id =", cam.key())
            total = q2.count()
            q2.filter("image_time >=", datetime.now().replace(day=1,hour=0,minute=0)) 
            thismonth = q2.count()
            startofweek = datetime.now().day - datetime.now().weekday() % 7
            q2.filter("image_time >=", datetime.now().replace(day=startofweek, hour=0, minute=0)) 
            thisweek = q2.count()
            q2.filter("image_time >=", datetime.now().replace(hour=0,minute=0)) 
            today = q2.count()

            q2 = CameraFrame.all()
            q2.filter("camera_id =", cam.key())
            q2.filter("image_time >=", datetime.now().replace(hour=0,minute=0) - timedelta(days=1))
            q2.filter("image_time <", datetime.now().replace(hour=0,minute=0))
            yesterday = q2.count()

            self.response.out.write("<tr><td>%s</td><td><a href='%s'>link</a></td><td><img src='/camera/livethumb?camera=%s' width=80 height=100></td><td>%d</td><td>%d</td><td>%d</td><td>%d</td><td>%d</td><td><a href='/camera/edit?camera=%s'>[Edit]</a> <a href='/camera/delete?camera=%s'>[Delete]</a> <a href='/camera/trigger?camera=%s'>[Trigger]</a></td></tr>" % 
                                    (cgi.escape(cam.title), cam.url, cam.key(), total, today, yesterday, thisweek, thismonth, cam.key(), cam.key(), cam.key()))

        self.response.out.write("</table>")
        self.response.out.write("<p><a href='/camera/add'>[Add new camera]</a></p>")


# Send back a scaled thumbnail of the last retrieved image from a camera.
class LiveThumbHandler(webapp.RequestHandler):
    def get(self):
        keyname = self.request.get('camera')
        if keyname is None:
            self.error(500)
            self.response.out.write("missing camera")
            return

        imgdata = memcache.get("camera{%s}" % keyname)
        if imgdata is not None:
            img = images.Image(image_data=imgdata)
            img.resize(width=80, height=100)
            img.im_feeling_lucky()
            thumbnail = img.execute_transforms(output_encoding=images.JPEG)

            self.response.headers['Content-Type'] = 'image/jpeg'
            self.response.out.write(thumbnail)
        else:            
            self.error(404)
            self.response.out.write("not found")
            return
            

#class LiveThumbStreamHandler(webapp.RequestHandler):
#    def get(self):
#        keyname = self.request.get('camera')
#        if keyname is None:
#            self.error(500)
#            self.response.out.write("missing camera")
#            return
#
#        imgdata = memcache.get("camera{%s}" % keyname)
#        if imgdata is not None:
#            img = images.Image(image_data=imgdata)
#            img.resize(width=80, height=100)
#            img.im_feeling_lucky()
#            thumbnail = img.execute_transforms(output_encoding=images.JPEG)
#
#            self.response.headers['Content-Type'] = 'Content-type: multipart/x-mixed-replace;boundary=End'
#            self.response.out.write('--End')
#            self.response.out.write('Content-type: image/jpeg')
#            self.response.out.write(thumbnail)
#            self.response.out.write('--End--')
#            #TODO
#        else:            
#            self.error(404)
#            self.response.out.write("not found")
#            return





class MainHandler(webapp.RequestHandler):
    def get(self):
        #self.response.headers['Content-Type'] = 'text/plain'
        self.response.out.write('Hello cows!')
        self.response.out.write('<a href="/summary">View the system</a>')



def main():
    application = webapp.WSGIApplication([('/', MainHandler),
                                          ('/summary', MainSummaryHandler),
                                          ('/camera/add', AddCameraSourceHandler),
                                          ('/camera/edit', EditCameraSourceHandler),
                                          ('/camera/delete', DeleteCameraSourceHandler),
                                          ('/camera/trigger', ImageFetcherTask), 
                                          ('/camera/livethumb', LiveThumbHandler),
                                          ('/tasks/poll_sources', ImageFetcherTask) ],
                                          ('/tasks/garbage_collector', GarbageCollectorTask) ],
                                         debug=True)
    util.run_wsgi_app(application)


if __name__ == '__main__':
    main()
