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
import png


# investigate python_precompiled
# Pure Python PNG parser -- http://packages.python.org/pypng/png.html
# Motion detection -- http://www.codeproject.com/KB/audio-video/Motion_Detection.aspx
# PIL http://www.pythonware.com/library/pil/handbook/introduction.htm

# YouTube upload -- http://code.google.com/apis/youtube/1.0/developers_guide_python.html#DirectUpload
# YouTube upload -- http://code.google.com/apis/youtube/2.0/developers_guide_protocol_direct_uploading.html#Direct_uploading

# sample cameras -- http://ipcctvsoft.blogspot.com/2010/07/list-of-ip-surveillance-cameras-found.html

# ----------------------------------------------------------------------

class CameraSource(db.Model):
    title = db.StringProperty(required=True)
    url = db.LinkProperty(required=True)
    # username/password auth
    poll_max_fps = db.IntegerProperty(default=1, required=True)
    alert_max_fps = db.IntegerProperty(default=10, required=True)
    creation_time = db.DateTimeProperty(auto_now_add=True)
    last_edited = db.DateTimeProperty()
    enabled = db.BooleanProperty(default=True,required=True)
    deleted = db.BooleanProperty(default=False,required=True)
    # mode?  modetect, record
    # frames before/after event
    num_secs_after = db.FloatProperty(default=2.0, required=True)
    # sensitivity


class CameraEvent(db.Model):
    camera_id = db.ReferenceProperty(CameraSource, required=True)
    event_start = db.DateTimeProperty(required=True)
    event_end = db.DateTimeProperty(required=True)
    motion_rating = db.RatingProperty()
    total_frames = db.IntegerProperty(default=0,required=True)
    last_motion_time = db.DateTimeProperty()
    comments = db.StringProperty()
    category = db.CategoryProperty()
    archived = db.BooleanProperty(default=False,required=True)
    deleted = db.BooleanProperty(default=False,required=True)
    viewed = db.BooleanProperty(default=False,required=True)


class CameraFrame(db.Model):
    camera_id = db.ReferenceProperty(CameraSource, required=True)
    event_id = db.ReferenceProperty(CameraEvent)
    image_time = db.DateTimeProperty(required=True)
    full_size_image = db.BlobProperty(required=True)



MAX_CAMERAS = 10
MODETECT_IMAGE_SIZE = 100

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
            capture_time = datetime.now()

            # TODO: check status code and content-type, handle exceptions
            #if response.status_code != 200:

            # store the fully image in memcache
            memcache.set("camera{%s}.lastimg_orig" % cam.key(), response.content)


            # retrieve the last background image
            lastimg_mopng = memcache.get("camera{%s}.lastimg_mopng" % cam.key())
            if lastimg_mopng != None:
                lastfloatdata = png.Reader(bytes=lastimg_mopng).asFloat()
            else:
                lastfloatdata = None


            # process the new image for motion detection
            img = images.Image(image_data=response.content)
            img.resize(width=MODETECT_IMAGE_SIZE, height=MODETECT_IMAGE_SIZE)
            img.im_feeling_lucky()
            mopng = img.execute_transforms(output_encoding=images.PNG)
            memcache.set("camera{%s}.lastimg_mopng" % cam.key(), mopng)
            floatdata = png.Reader(bytes=mopng).asFloat()

            # compute the image difference between lastfloatdata & floatdata
            newdata = list()
            for row in list(floatdata[2]):
                newdata.append(list(row))

            # TODO
            motion_rating = 0
            motion_found = False


            # add to an existing event if needed
            eventkey = memcache.get("camera{%s}.eventkey" % cam.key())
            if eventkey is not None:
                event = CameraEvent.get(db.Key(eventkey))

                # store frame in the database
                frame = CameraFrame(camera_id = cam.key(),
                                    event_id = event.key(),
                                    full_size_image = response.content,
                                    image_time = capture_time)
                frame.put()

                # update the event in the database
                if motion_rating > event.motion_rating:
                    event.motion_rating = motion_rating
                if motion_found:
                    event.last_motion_time = capture_time
                event.total_frames = event.total_frames + 1
                event.event_end = capture_time
                event.put()

                # stop the event, if it's time
                if (not motion_found) and (capture_time - event.last_motion_time).total_seconds() > cam.num_secs_after:
                    memcache.delete("camera{%s}.eventkey" % cam.key())
            elif motion_found:
                # start a new event
                event = CameraEvent(total_frames = 1,
                                    event_start = capture_time,
                                    event_end = capture_time,
                                    motion_rating = motion_rating,
                                    last_motion_time = capture_time,
                                    camera_id = cam.key(),
                                    viewed = False,
                                    archived = False,
                                    deleted = False)
                event.put()

                # store frame in the database
                frame = CameraFrame(camera_id = cam.key(),
                                    event_id = event.key(),
                                    full_size_image = response.content,
                                    image_time = capture_time)
                frame.put()

                # keep the event open.
                memcache.set("camera{%s}.eventkey" % cam.key(), event.key())



        self.response.headers['Content-Type'] = 'text/html'
        self.response.out.write("done")





class AddCameraSourceHandler(webapp.RequestHandler):
    def get(self):
        #url = "http://cowpad.dyn-o-saur.com:8080/xxxxx"
        cam = CameraSource(title="My Camera",
                           url="http://www.bovine.net/~jlawson/webcam/homecam.jpg",
                           poll_max_fps = 1,
                           alert_max_fps = 10,
                           creation_time = datetime.now(),
                           enabled = True,
                           deleted = False,
                           num_secs_after = 2.0)
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

        imgdata = memcache.get("camera{%s}.lastimg_orig" % keyname)
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
            


# http://www.jpegcameras.com/
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
#             Content-Length: 9290
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
                                          ('/tasks/poll_sources', ImageFetcherTask),
                                          ('/tasks/garbage_collector', GarbageCollectorTask) ],
                                         debug=True)
    util.run_wsgi_app(application)


if __name__ == '__main__':
    main()
