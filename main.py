#!/usr/bin/env python

from google.appengine.ext import webapp, db
from google.appengine.ext.webapp import util, template
from google.appengine.api import memcache, urlfetch, images
from datetime import datetime, timedelta
import os
import urllib
import cgi
import png



MAX_CAMERAS = 10
MODETECT_IMAGE_SIZE = 100
MODETECT_EWMA_ALPHA = 0.25

# ----------------------------------------------------------------------

class CameraSource(db.Model):
    title = db.StringProperty(required=True)
    url = db.LinkProperty(required=True)
    # TODO: username/password auth
    poll_max_fps = db.IntegerProperty(default=1, required=True)
    alert_max_fps = db.IntegerProperty(default=10, required=True)
    creation_time = db.DateTimeProperty(auto_now_add=True)
    last_edited = db.DateTimeProperty()
    enabled = db.BooleanProperty(default=True,required=True)
    deleted = db.BooleanProperty(default=False,required=True)
    # TODO: mode?  modetect, record
    # TODO: frames before/after event
    num_secs_after = db.FloatProperty(default=2.0, required=True)
    # TODO: sensitivity


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




# ----------------------------------------------------------------------

# This is the background handler that gets invoked to poll images for motion.
class ImageFetcherTask(webapp.RequestHandler):

    def detectMotion(self, prevImage, curImage):
        if prevImage == None or curImage == None:
            return 0.1
        elif len(prevImage) != 4 or len(curImage) != 4:
            return 0.2
        elif prevImage[0] != curImage[0] or prevImage[1] != curImage[1]:
            return 0.3

        # compute the summed total of all pixel changes.
        diffAmt = 0.0
        try:
            curRowIter, prevRowIter = iter(curImage[2]), iter(prevImage[2])
            while True:
                curRow, prevRow = curRowIter.next(), prevRowIter.next()
                try:
                    curColIter, prevColIter = iter(curRow), iter(prevRow)
                    while True:
                        curCol, prevCol = curColIter.next(), prevColIter.next()
                        diffAmt = diffAmt + abs(curCol - prevCol)
                except StopIteration:
                    pass
        except StopIteration:
            pass

        # scale the total into a percentage of image change between 0 and 100.
        return 100.0 * diffAmt / (curImage[0] * curImage[1] * 3.0)


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
            motion_pct_change = self.detectMotion(lastfloatdata, floatdata)


            # compute an exponentially-weighted moving average (EWMA).
            ewma = memcache.get("camera{%s}.ewma" % cam.key())
            if ewma != None:
                ewma = MODETECT_EWMA_ALPHA * motion_pct_change + (1.0 - MODETECT_EWMA_ALPHA) * ewma
            else:
                ewma = motion_pct_change
            memcache.set("camera{%s}.ewma" % cam.key(), ewma)


            # use the EWMA to compute a score of the motion.
            if ewma != 0:
                motion_rating = abs(100.0 * (motion_pct_change - ewma) / ewma)
            else:
                motion_rating = 0

            self.response.out.write("pct_change = %f, ewma = %f, motion_rating = %f<br>" % (motion_pct_change, ewma, motion_rating))
            if motion_rating > 100.0:
                motion_rating = 100
            else:
                motion_rating = int(round(motion_rating))

            motion_found = (motion_rating > 50)


            # add to an existing event if needed
            eventkey = memcache.get("camera{%s}.eventkey" % cam.key())
            if eventkey == "trigger":
                motion_found = True
                eventkey = None

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
                td = (capture_time - event.last_motion_time)
                total_seconds = (td.microseconds + (td.seconds + td.days * 24 * 3600) * 10**6) / 10**6
                if (not motion_found) and (total_seconds > cam.num_secs_after):
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
                memcache.set("camera{%s}.eventkey" % cam.key(), str(event.key()))



        #self.response.headers['Content-Type'] = 'text/html'
        self.response.out.write("done")


class TriggerCameraSourceHandler(webapp.RequestHandler):
    def get(self):
        keyname = self.request.get('camera')
        if keyname is not None:
            cam = CameraSource.get(db.Key(keyname))
            memcache.set("camera{%s}.eventkey" % cam.key(), "trigger")
            self.response.out.write("triggered!")



class AddCameraSourceHandler(webapp.RequestHandler):
    def get(self):
        #newurl = "http://www.bovine.net/~jlawson/webcam/homecam.jpg"
        newurl = "http://cam-fiddlers.athens.edu/axis-cgi/jpg/image.cgi"
        #newurl = "http://cowpad.dyn-o-saur.com:8080/xxxxx"
        cam = CameraSource(title="My Camera2",
                           url=newurl,
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

        for cam in results:
            q2 = CameraFrame.all()
            q2.filter("camera_id =", cam.key())
            cam.total = q2.count()
            q2.filter("image_time >=", datetime.now().replace(day=1,hour=0,minute=0)) 
            cam.thismonth = q2.count()
            startofweek = datetime.now().day - datetime.now().weekday() % 7
            q2.filter("image_time >=", datetime.now().replace(day=startofweek, hour=0, minute=0)) 
            cam.thisweek = q2.count()
            q2.filter("image_time >=", datetime.now().replace(hour=0,minute=0)) 
            cam.today = q2.count()

            q2 = CameraFrame.all()
            q2.filter("camera_id =", cam.key())
            q2.filter("image_time >=", datetime.now().replace(hour=0,minute=0) - timedelta(days=1))
            q2.filter("image_time <", datetime.now().replace(hour=0,minute=0))
            cam.yesterday = q2.count()


        template_values = {
            'camlist': results,
            }

        path = os.path.join(os.path.dirname(__file__), 'summary.html')
        self.response.out.write(template.render(path, template_values))


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
                                          ('/camera/trigger', TriggerCameraSourceHandler), 
                                          ('/camera/livethumb', LiveThumbHandler),
                                          ('/tasks/poll_sources', ImageFetcherTask),
                                          ('/tasks/garbage_collector', GarbageCollectorTask) ],
                                         debug=True)
    util.run_wsgi_app(application)


if __name__ == '__main__':
    main()
