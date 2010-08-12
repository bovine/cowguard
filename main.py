#!/usr/bin/env python

from google.appengine.ext import webapp
from google.appengine.ext.webapp import util
from google.appengine.api import memcache
from google.appengine.api import urlfetch 
from google.appengine.api import images
from google.appengine.ext import db
from datetime import datetime
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
    creation_time = db.DateTimeProperty()
    last_edited = db.DateTimeProperty()
    enabled = db.BooleanProperty()
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

# ----------------------------------------------------------------------

# This is the background handler that gets invoked to poll images for motion.
class ImageFetcherTask(webapp.RequestHandler):
    def get(self):
        q = CameraSource.all()
        q.filter("enabled =",True)

        results = q.fetch(5)
        # TODO: needs to keep looping for about a minute, honoring the poll_max_fps
        for p in results:
            response = urlfetch.fetch(p.url, payload=None, method=urlfetch.GET, headers={}, allow_truncated=False, follow_redirects=True, deadline=5)

            # TODO: check status code and content-type, handle exceptions
            #if response.status_code != 200:

            # store in memcache
            memcache.set("camera{%s}" % p.key(), response.content)

            # store in the database
            s2 = CameraFrame()
            s2.camera_id = p.key()
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
        cam.put()

        self.response.out.write("added")



# Display a HTML status table summarizing all cameras currently in the system and the number of recent events.
class MainSummaryHandler(webapp.RequestHandler):
    def get(self):
        q = CameraSource.all()
        q.order("-creation_time")

        results = q.fetch(5)

        self.response.headers['Content-Type'] = 'text/html'
        #self.response.out.write("<script src='http://ajax.googleapis.com/ajax/libs/jquery/1.4.2/jquery.min.js' type='text/javascript'></script>");
        self.response.out.write("<table border=1>")
        self.response.out.write("<tr><th>Key</th><th>Title</th><th>Url</th></tr>")
        for p in results:
            #cgi.escape
            #urllib.quote_plus
            self.response.out.write("<tr><td>%s</td><td>%s</td><td><a href='%s'>link</a></td><td><img src='/livethumb?camera=%s' width=80 height=100></tr>" % (p.key(), p.title, p.url, p.key() ))

        self.response.out.write("</table>")


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
        self.response.headers['Content-Type'] = 'text/plain'
        self.response.out.write('Hello cows!')



def main():
    application = webapp.WSGIApplication([('/', MainHandler),
                                          ('/summary', MainSummaryHandler),
                                          ('/addcamera', AddCameraSourceHandler),
                                          ('/livethumb', LiveThumbHandler),
                                          ('/tasks/poll_sources', ImageFetcherTask) ],
                                         debug=True)
    util.run_wsgi_app(application)


if __name__ == '__main__':
    main()
