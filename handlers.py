#!/usr/bin/env python

from google.appengine.ext import webapp, db
from google.appengine.ext.webapp import util, template
from google.appengine.api import memcache, urlfetch, images, users
from google.appengine.api.labs import taskqueue
from google.appengine.runtime import DeadlineExceededError
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

# threshold value to consider motion detected (0-100, higher values requiring
# greater amounts of motion to trigger).
MODETECT_THRESHOLD = 50


# ----------------------------------------------------------------------

# This is the background handler that gets invoked to poll images for motion.
class ImageFetcherTask(webapp.RequestHandler):

    # Low-level helper used to compare two image arrays and return a floating-point
    # value representing the amount of motion found.  The actual numeric range returned
    # here is not really important, since the relative change between different values
    # will be scaled and then compared against the threshold setting.
    def detectMotion(self, prevImage, curImage):
        # make sure both arguments are a 4-tuple and the images are the same size.
        # The 4 elements should be (width,height,pixels,info)
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

        # Scale the total into a ranking of image change between 0 and 1,000,000.
        return 1000000.0 * diffAmt / (curImage[0] * curImage[1] * 3.0)


    # Main worker of camera capturing and detection logic.
    def pollCamera(self, cam):

        # capture an image from the remote camera.
        response = urlfetch.fetch(cam.url, payload=None, method=urlfetch.GET, headers={}, allow_truncated=False, follow_redirects=True, deadline=5)
        capture_time = datetime.now()

        # TODO: check status code and content-type, handle exceptions
        #if response.status_code != 200:

        # store the full frame in memcache.
        memcache.set("camera{%s}.lastimg_orig" % cam.key(), response.content)


        # retrieve the processed version of the last frame.
        lastimg_mopng = memcache.get("camera{%s}.lastimg_mopng" % cam.key())
        if lastimg_mopng != None:
            lastfloatdata = png.Reader(bytes=lastimg_mopng).asFloat()
        else:
            lastfloatdata = None


        # Process the new frame for motion detection by adjusting constrast,
        # resizing to a very small thumbnail, converting to PNG, and then
        # obtaining raw pixel data from the PNG using pypng.
        img = images.Image(image_data=response.content)
        img.im_feeling_lucky()
        img.resize(width=MODETECT_IMAGE_SIZE, height=MODETECT_IMAGE_SIZE)
        mopng = img.execute_transforms(output_encoding=images.PNG)
        memcache.set("camera{%s}.lastimg_mopng" % cam.key(), mopng)
        floatdata = png.Reader(bytes=mopng).asFloat()


        # compute the frame difference between lastfloatdata & floatdata
        motion_amt_change = self.detectMotion(lastfloatdata, floatdata)


        # compute an exponentially-weighted moving average (EWMA).
        ewma = memcache.get("camera{%s}.ewma" % cam.key())
        if ewma != None:
            ewma = MODETECT_EWMA_ALPHA * motion_amt_change + (1.0 - MODETECT_EWMA_ALPHA) * ewma
        else:
            ewma = motion_amt_change
        memcache.set("camera{%s}.ewma" % cam.key(), ewma)


        # use the EWMA to compute a score of the motion.
        if ewma != 0 and motion_amt_change != 0:
            motion_rating = abs(100.0 * (motion_amt_change - ewma) / ewma)
        else:
            motion_rating = 0

        self.response.out.write("amt_change = %f, ewma = %f, motion_rating = %f\n" % (motion_amt_change, ewma, motion_rating))

        # clamp the maximum range, and ensure it is an integer.
        if motion_rating > 100.0:
            motion_rating = 100
        else:
            motion_rating = int(round(motion_rating))

        # make a boolean decision about whether there is motion or not.
        # TODO: this should use a user-controlled setting in the CameraSource
        motion_found = (motion_rating > MODETECT_THRESHOLD)


        # add to an existing event if needed
        eventkey = memcache.get("camera{%s}.eventkey" % cam.key())
        if eventkey == "trigger":
            motion_found = True
            eventkey = None

        if eventkey is not None:
            #
            # An existing event was found, so just add this frame to it.
            #
            event = CameraEvent.get(db.Key(eventkey))

            # store frame in the database
            frame = CameraFrame(camera_id = cam.key(),
                                event_id = event.key(),
                                full_size_image = response.content,
                                image_time = capture_time,
                                alarmed = motion_found,
                                motion_rating = motion_rating)
            frame.put()

            # update the event in the database
            if motion_rating > event.max_motion_rating:
                event.max_motion_rating = motion_rating

            event.total_frames = event.total_frames + 1
            event.total_motion_rating = event.total_motion_rating + motion_rating
            event.avg_motion_rating = event.total_motion_rating / event.total_frames

            event.event_end = capture_time
            if motion_found:
                event.alarm_frames = event.alarm_frames + 1
                event.last_motion_time = capture_time

            event.put()

            # stop the event, if it's time
            td = (capture_time - event.last_motion_time)
            total_seconds = (td.microseconds + (td.seconds + td.days * 24 * 3600) * 10**6) / 10**6
            if (not motion_found) and (total_seconds > cam.num_secs_after):
                memcache.delete("camera{%s}.eventkey" % cam.key())

        elif motion_found:
            #
            # No existing event found, so start a new event.
            #
            event = CameraEvent(total_frames = 1,
                                alarm_frames = 1,
                                event_start = capture_time,
                                event_end = capture_time,
                                max_motion_rating = motion_rating,
                                total_motion_rating = motion_rating,
                                avg_motion_rating = motion_rating,
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
                                image_time = capture_time,
                                alarmed = motion_found,
                                motion_rating = motion_rating)
            frame.put()

            # keep the event open.
            memcache.set("camera{%s}.eventkey" % cam.key(), str(event.key()))


    def get(self):
        self.response.headers['Content-Type'] = 'text/plain'

        # iterate through all cameras, capturing an image and detecting motion.
        q = CameraSource.all()
        q.filter("enabled =",True).filter("deleted =",False)

        # TODO: needs to keep looping for about up to 30 seconds, honoring the poll_max_fps
        for cam in q.fetch(MAX_CAMERAS):
            self.pollCamera(cam)


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

class DeleteCameraEventHandler(webapp.RequestHandler):
    def post(self):
        keyname = self.request.get('event')
        if keyname is not None:
            event = CameraEvent.get(db.Key(keyname))
            event.deleted = True
            event.put()

        self.response.out.write("deleted")

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
        numDeleted = 0
        try:
            # Look for CameraEvents marked deleted
            q = CameraEvent.all()
            q.filter("deleted =",True)
            for event in q.fetch(10):
                # Delete any frames belonging to this deleted CameraSource.
                q2 = CameraFrame.all()
                q2.filter("event_id =", event.key())
                if q2.count() > 0:
                    for frame in q2.fetch(500):
                        frame.delete()
                        numDeleted = numDeleted + 1
                else:
                    # No more frames belonging to this event, so delete it too.
                    event.delete()
                    numDeleted = numDeleted + 1

            # Look for CameraSources marked deleted
            q = CameraSource.all()
            q.filter("deleted =",True)
            for cam in q.fetch(10):
                # Mark any events belonging to this deleted CameraSource as deleted.
                q2 = CameraEvent.all()
                q2.filter("camera_id =", cam.key()).filter("deleted =",False)
                for event in q2.fetch(100):
                    numDeleted = numDeleted + 1
                    event.deleted = True
                    event.put()

                # Delete any frames belonging to this deleted CameraSource.
                q2 = CameraFrame.all()
                q2.filter("camera_id =", cam.key())
                for frame in q2.fetch(500):
                    frame.delete()
                    numDeleted = numDeleted + 1

                # Only delete the camera itself if nothing else needed to be done.
                if numDeleted == 0:
                    cam.delete()
                    numDeleted = numDeleted + 1

        except DeadlineExceededError:
            pass

        self.response.headers['Content-Type'] = 'text/plain'
        self.response.out.write("Deleted %d objects." % numDeleted)

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
        q2.filter("camera_id =", cam.key()).filter("deleted =", False).order("-event_start")
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
            'user': users.get_current_user(),
            'logout_url': users.create_logout_url('/'),
            'camera': cam,
            'eventlist': results,
            'timenow': datetime.utcnow(),
            }

        path = os.path.join(os.path.dirname(__file__), 'inc/browse.html')
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


            # TODO: deleted frames should not be included in these counts.
            qf = CameraFrame.all()
            qf.filter("camera_id =", cam.key())
            qe = CameraEvent.all()
            qe.filter("deleted =", False).filter("camera_id =", cam.key())
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
            qe.filter("deleted =", False)
            qe.filter("camera_id =", cam.key())
            qe.filter("event_start >=", datetime.now().replace(hour=0,minute=0) - timedelta(days=1))
            qe.filter("event_start <", datetime.now().replace(hour=0,minute=0))
            cam.fyesterday = qf.count()
            cam.eyesterday = qe.count()


        template_values = {
            'user': users.get_current_user(),
            'logout_url': users.create_logout_url('/'),
            'camlist': results,
            'timenow': datetime.utcnow(),
            }

        path = os.path.join(os.path.dirname(__file__), 'inc/summary.html')
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

class GetImgSeqEventHandler(webapp.RequestHandler):
    def get(self):
        keyname = self.request.get('event')
        if keyname is None:
            self.error(500)
            self.response.out.write("missing event")
            return

        event = CameraEvent.get(db.Key(keyname))
        if event is None or event.deleted == True:
            self.error(404)
            self.response.out.write("unknown event")
            return

        q2 = CameraFrame.all()
        #q2.filter("event_id =", event.key()).order("image_time")
        q2.filter("camera_id =", event.camera_id).filter("image_time >=", event.event_start).filter("image_time <=", event.event_end).order("image_time")

        template_values = {
            'user': users.get_current_user(),
            'logout_url': users.create_logout_url('/'),
            'event': event,
            'framelist': q2.fetch(100),
            'timenow': datetime.utcnow(),
            }

        path = os.path.join(os.path.dirname(__file__), 'inc/imgseq.html')
        self.response.out.write(template.render(path, template_values))

# ----------------------------------------------------------------------

# Send back a scaled thumbnail of any CameraFrame.
class CameraFrameThumbHandler(webapp.RequestHandler):
    def get(self):
        keyname = self.request.get('frame')
        if keyname is None:
            self.error(500)
            self.response.out.write("missing frame")
            return

        frame = CameraFrame.get(db.Key(keyname))
        if frame is not None:
            imgdata = frame.full_size_image
        else:
            imgdata = None

        if imgdata is None:
            # requested image was not found.
            self.error(404)
            self.response.out.write("not found")
            return
        elif self.request.headers.has_key('If-Modified-Since'):
            # frames are never modified, so always say unmodified.
            self.error(304)
            return
        else:
            img = images.Image(image_data=imgdata)
            #img.resize(width=80, height=100)
            img.im_feeling_lucky()
            thumbnail = img.execute_transforms(output_encoding=images.JPEG)

            self.response.headers['Content-Type'] = 'image/jpeg'
            current_time = datetime.utcnow()
            self.response.headers['Last-Modified'] = frame.image_time.strftime('%a, %d %b %Y %H:%M:%S GMT')
            self.response.headers['Expires'] = current_time + timedelta(days=30)
            self.response.headers['Cache-Control']  = 'public, max-age=315360000'
            self.response.headers['Date']           = current_time
            self.response.out.write(thumbnail)


# ----------------------------------------------------------------------


class MainHandler(webapp.RequestHandler):
    def get(self):
        #self.response.headers['Content-Type'] = 'text/plain'
        self.response.out.write('Hello cows!')
        self.response.out.write('<a href="/summary">View the system</a>')


