#!/usr/bin/env python

from google.appengine.ext import webapp, db
from google.appengine.ext.webapp import util, template
from google.appengine.api import memcache, urlfetch, images, users
from datetime import datetime, timedelta
import os
import urllib
import cgi
import png
from schema import CameraSource, CameraEvent, CameraFrame


# maximum number of enabled camera supported.
MAX_CAMERAS = 10

# image size (pixels) that all motion-detection images are scaled to.
MODETECT_IMAGE_SIZE = 100

# alpha factor used in the exponentially weighted moving average.
# must be between 0.0 and 1.0, with higher values giving faster response.
MODETECT_EWMA_ALPHA = 0.25



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
            if ewma != 0 and motion_pct_change != 0:
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


# ----------------------------------------------------------------------

class TriggerCameraSourceHandler(webapp.RequestHandler):
    def get(self):
        keyname = self.request.get('camera')
        if keyname is not None:
            cam = CameraSource.get(db.Key(keyname))
            memcache.set("camera{%s}.eventkey" % cam.key(), "trigger")
            self.response.out.write("triggered!")

# ----------------------------------------------------------------------


class AddCameraSourceHandler(webapp.RequestHandler):
    def post(self):
        cam_name = self.request.get('name')
        cam_url = self.request.get('url')
        cam_enabled = bool(int(self.request.get('enabled')))
        cam_poll_max_fps = int(self.request.get('poll_max_fps'))
        cam_alert_max_fps = int(self.request.get('alert_max_fps'))
        cam_num_secs_after = float(self.request.get('num_secs_after'))

        cam = CameraSource(name=cam_name,
                           url=cam_url,
                           poll_max_fps = cam_poll_max_fps,
                           alert_max_fps = cam_alert_max_fps,
                           creation_time = datetime.now(),
                           enabled = cam_enabled,
                           deleted = False,
                           num_secs_after = cam_num_secs_after)
        cam.put()

        self.response.out.write("added=%s" % cam.key())

# ----------------------------------------------------------------------

class EditCameraSourceHandler(webapp.RequestHandler):
    def post(self):
        keyname = self.request.get('camera')
        if keyname is not None:
            cam = CameraSource.get(db.Key(keyname))

        cmd = self.request.get('cmd')
        if cmd == 'get':
            self.response.headers['Content-Type'] = 'text/json'
            self.response.out.write("{")
            self.response.out.write(' "key": "%s",' % cam.key())
            self.response.out.write(' "name": "%s",' % cam.name)
            self.response.out.write(' "url": "%s",' % cam.url)
            self.response.out.write(' "enabled": %d,' % cam.enabled)
            self.response.out.write(' "poll_max_fps": %d,' % cam.poll_max_fps)
            self.response.out.write(' "alert_max_fps": %d,' % cam.alert_max_fps)
            self.response.out.write(' "num_secs_after": %f' % cam.num_secs_after)
            self.response.out.write("}")

        elif cmd == 'save':
            cam.name = self.request.get('name')
            cam.url = self.request.get('url')
            cam.enabled = bool(int(self.request.get('enabled')))
            cam.poll_max_fps = int(self.request.get('poll_max_fps'))
            cam.alert_max_fps = int(self.request.get('alert_max_fps'))
            cam.num_secs_after = float(self.request.get('num_secs_after'))
            cam.put()
            self.response.out.write("success")

        else:
            self.error(500)
            return


# ----------------------------------------------------------------------

class DeleteCameraSourceHandler(webapp.RequestHandler):
    def post(self):
        keyname = self.request.get('camera')
        if keyname is not None:
            cam = CameraSource.get(db.Key(keyname))
            cam.deleted = True
            cam.put()

        self.response.out.write("deleted")

# ----------------------------------------------------------------------

class GarbageCollectorTask(webapp.RequestHandler):
    def get(self):
        # Look for CameraSources marked deleted
        # Look for Cameraevents marked deleted
        self.response.out.write("unimplemented")
        
# ----------------------------------------------------------------------


class BrowseEventsHandler(webapp.RequestHandler):
    def get(self):
        keyname = self.request.get('camera')
        if keyname is not None:
            cam = CameraSource.get(db.Key(keyname))
        else:
            self.error(500)
            return

        period = self.request.get('period')
        q2 = CameraEvent.all()
        q2.filter("camera_id =", cam.key())
        if period == "today":
            q2.filter("event_start >=", datetime.now().replace(hour=0,minute=0)) 
        elif period == "yesterday":
            q2.filter("event_start >=", datetime.now().replace(hour=0,minute=0) - timedelta(days=1))
            q2.filter("event_start <", datetime.now().replace(hour=0,minute=0))
        elif period == "week":
            startofweek = datetime.now().day - datetime.now().weekday() % 7
            q2.filter("event_start >=", datetime.now().replace(day=startofweek, hour=0, minute=0)) 
        elif period == "month":
            q2.filter("event_start >=", datetime.now().replace(day=1,hour=0,minute=0)) 
        elif period == "all":
            pass
        else:
            return

        #cam.total = q2.count()
        results = q2.fetch(1000)

        for event in results:
            event.duration_text = (event.event_end - event.event_start)


        template_values = {
            'user_name': users.get_current_user(),
            'logout_url': users.create_logout_url('/'),
            'camera': cam,
            'eventlist': results,
            }

        path = os.path.join(os.path.dirname(__file__), 'browse.html')
        self.response.out.write(template.render(path, template_values))

        
# ----------------------------------------------------------------------


# Display a HTML status table summarizing all cameras currently in the system and the number of recent events.
class MainSummaryHandler(webapp.RequestHandler):
    def get(self):
        q = CameraSource.all()
        q.filter("deleted =", False).order("-creation_time")

        results = q.fetch(MAX_CAMERAS)

        for cam in results:

            cam.status_text = "---"
            # TODO: cam.enabled, cam.last_poll_time, cam.last_poll_result


            qf = CameraFrame.all()            
            qf.filter("camera_id =", cam.key())
            qe = CameraEvent.all()
            qe.filter("camera_id =", cam.key())
            cam.ftotal = qf.count()
            cam.etotal = qe.count()

            qf.filter("image_time >=", datetime.now().replace(day=1,hour=0,minute=0)) 
            qe.filter("event_start >=", datetime.now().replace(day=1,hour=0,minute=0)) 
            cam.fthismonth = qf.count()
            cam.ethismonth = qe.count()

            startofweek = datetime.now().day - datetime.now().weekday() % 7
            qf.filter("image_time >=", datetime.now().replace(day=startofweek, hour=0, minute=0)) 
            qe.filter("event_start >=", datetime.now().replace(day=startofweek, hour=0, minute=0)) 
            cam.fthisweek = qf.count()
            cam.ethisweek = qe.count()

            qf.filter("image_time >=", datetime.now().replace(hour=0,minute=0)) 
            qe.filter("event_start >=", datetime.now().replace(hour=0,minute=0)) 
            cam.ftoday = qf.count()
            cam.etoday = qe.count()

            qf = CameraFrame.all()
            qf.filter("camera_id =", cam.key())
            qf.filter("image_time >=", datetime.now().replace(hour=0,minute=0) - timedelta(days=1))
            qf.filter("image_time <", datetime.now().replace(hour=0,minute=0))
            qe = CameraEvent.all()
            qe.filter("camera_id =", cam.key())
            qe.filter("event_start >=", datetime.now().replace(hour=0,minute=0) - timedelta(days=1))
            qe.filter("event_start <", datetime.now().replace(hour=0,minute=0))
            cam.fyesterday = qf.count()
            cam.eyesterday = qe.count()


        template_values = {
            'user_name': users.get_current_user(),
            'logout_url': users.create_logout_url('/'),
            'camlist': results,
            }

        path = os.path.join(os.path.dirname(__file__), 'summary.html')
        self.response.out.write(template.render(path, template_values))

# ----------------------------------------------------------------------

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
            
# ----------------------------------------------------------------------

# TODO: It doesn't seem like there is any way to do this type of pushed image updating in App Engine?

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



# ----------------------------------------------------------------------


class MainHandler(webapp.RequestHandler):
    def get(self):
        #self.response.headers['Content-Type'] = 'text/plain'
        self.response.out.write('Hello cows!')
        self.response.out.write('<a href="/summary">View the system</a>')


