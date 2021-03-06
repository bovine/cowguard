#!/usr/bin/env python

from google.appengine.ext import webapp, db
from google.appengine.ext.webapp import util, template
from google.appengine.api import memcache, urlfetch, images, users
from google.appengine.api.labs import taskqueue
from google.appengine.runtime import DeadlineExceededError, apiproxy_errors
from datetime import datetime, timedelta
import os, urllib, cgi, png, time, base64
from schema import CameraSource, CameraEvent, CameraFrame


# maximum number of enabled cameras supported.
MAX_CAMERAS = 10

# number of seconds before the timeout of a HTTP GET for the camera image.
CAMFETCH_TIMEOUT = 10

# image size (pixels) that all motion-detection images are scaled to.
MODETECT_IMAGE_SIZE = 100

# alpha factor used in the exponentially weighted moving average.
# must be between 0.0 and 1.0, with higher values giving faster response.
MODETECT_EWMA_ALPHA = 0.25

# threshold value to consider motion detected (0-100, higher values requiring
# greater amounts of motion to trigger).
MODETECT_THRESHOLD = 50

# how many seconds to loop before exiting (should be less than 600 seconds
# to avoid hitting the AppEngine execution limit).
MODETECT_RUNTIME_LIMIT = 595


# ----------------------------------------------------------------------

# This is invoked periodically by cron.
class ImageWakeupTask(webapp.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/plain'

        # Look for any cameras that have not polled recently.
        q = CameraSource.all()
        q.filter("enabled =",True).filter("deleted =",False)
        clocknow = datetime.now()
        for cam in q.fetch(MAX_CAMERAS):
            lastclock = memcache.get("camera{%s}.lastpoll_time" % cam.key())
            if lastclock is None or (clocknow - lastclock) > timedelta(minutes=5):
                # Queue a task to re-start polling for this camera.
                ImageFetcherTask.queueTask(cam.key())

        self.response.out.write("done")


# ----------------------------------------------------------------------

# This is the background handler that gets invoked to poll images for motion.
class ImageFetcherTask(webapp.RequestHandler):

    # Queue a task to perform another camera poll.
    @staticmethod
    def queueTask(camkey, delay=0):
        taskqueue.add(queue_name="poll-source-queue",            
                    url="/tasks/poll_sources",
                    method="POST",
                    countdown=delay,
                    params=dict(camera=camkey, epoch=time.mktime(datetime.now().timetuple()) ),
                    transactional=False)


    # low-level method to capture an image from the remote camera.
    def captureImage(self, cam):
        request_headers = {}
        if cam.authuser and cam.authpass:
            basic_auth = base64.encodestring('%s:%s' % (cam.authuser, cam.authpass)).rstrip()
            request_headers["Authorization"] = 'Basic %s' % basic_auth
            
        response = urlfetch.fetch(cam.url, payload=None, method=urlfetch.GET, 
                                  headers=request_headers, allow_truncated=False,
                                  follow_redirects=True, deadline=CAMFETCH_TIMEOUT)

        if response.status_code != 200 or response.content_was_truncated:
            self.response.out.write("Got unsuccessful response")
            return None
            
        contentType = response.headers['Content-Type']
        if contentType is None or not contentType.startswith('image/'):
            self.response.out.write("Got wrong content-type")
            return None
        
        return response    

        
    # Low-level helper used to compare two image arrays and return a floating-point
    # value representing the amount of motion found.  The actual numeric range returned
    # here is not really important, since the relative change between different values
    # will be scaled and then compared against the threshold setting.
    def compareFrames(self, prevImage, curImage):
        # make sure both arguments are a 4-tuple and the images are the same size.
        # The 4 elements should be (width,height,pixels,info)
        if prevImage is None or curImage is None:
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
                        diffAmt += abs(curCol - prevCol)
                except StopIteration:
                    pass
        except StopIteration:
            pass

        # Scale the total into a ranking of image change between 0 and 1,000,000.
        return 1000000.0 * diffAmt / (curImage[0] * curImage[1] * 3.0)


    # Medium-level helper used to make a boolean decision about whether there is 
    # currently motion found in a newly captured image.
    def detectMotion(self, cam, imgdata):

        # retrieve the processed version of the last frame.
        lastimg_mopng = memcache.get("camera{%s}.lastimg_mopng" % cam.key())
        if lastimg_mopng is not None:
            lastfloatdata = png.Reader(bytes=lastimg_mopng).asFloat()
        else:
            lastfloatdata = None


        # Process the new frame for motion detection by adjusting constrast,
        # resizing to a very small thumbnail, converting to PNG, and then
        # obtaining raw pixel data from the PNG using pypng.
        img = images.Image(image_data=imgdata)
        img.im_feeling_lucky()
        img.resize(width=MODETECT_IMAGE_SIZE, height=MODETECT_IMAGE_SIZE)
        mopng = img.execute_transforms(output_encoding=images.PNG)
        memcache.set("camera{%s}.lastimg_mopng" % cam.key(), mopng)
        floatdata = png.Reader(bytes=mopng).asFloat()


        # compute the frame difference between lastfloatdata & floatdata
        motion_amt_change = self.compareFrames(lastfloatdata, floatdata)


        # compute an exponentially-weighted moving average (EWMA).
        ewma = memcache.get("camera{%s}.ewma" % cam.key())
        if ewma is not None:
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

        return (motion_rating, motion_found)


    # Main worker of camera capturing and detection logic.
    # Returns True when in an alarmed event state.
    def pollCamera(self, cam):
        # update the last time we attempted to poll this camera.
        # do this first in case we hit an exception while attempting to poll.
        memcache.set("camera{%s}.lastpoll_time" % cam.key(), datetime.now())

        # capture an image from the remote camera.
        response = self.captureImage(cam)
        capture_time = datetime.now()
        if response is None:
            return False
        
        # store the full frame in memcache.
        memcache.set("camera{%s}.lastimg_orig" % cam.key(), response.content)
        memcache.set("camera{%s}.lastimg_time" % cam.key(), capture_time)

        # decide whether there is motion found.
        (motion_rating, motion_found) = self.detectMotion(cam, response.content)
        
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

            event.total_frames += 1
            event.total_motion_rating += motion_rating
            event.avg_motion_rating = event.total_motion_rating / event.total_frames

            event.event_end = capture_time
            if motion_found:
                event.alarm_frames += 1
                event.last_motion_time = capture_time

            event.put()

            # stop the event, if it's time
            td = (capture_time - event.last_motion_time)
            total_seconds = (td.microseconds + (td.seconds + td.days * 24 * 3600) * 10**6) / 10**6
            if (not motion_found) and (total_seconds > cam.num_secs_after):
                memcache.delete("camera{%s}.eventkey" % cam.key())
                return False

            return True

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
            return True
        else:
            # No motion found and not currently in an event.
            return False
            
            
    # This is the callback invoked by the task queue to poll a single camera in a loop.
    def post(self):
        proc_start_time = datetime.now()
        self.response.headers['Content-Type'] = 'text/plain'

        # ensure that this task isn't too old (in case this task got requeued).
        epoch = self.request.get('epoch')
        if epoch is None:
            self.response.out.write("no epoch specified")
            return
        if (proc_start_time - datetime.fromtimestamp(float(epoch))) > timedelta(seconds=30):
            self.response.out.write("task too old")
            return
            

        keyname = self.request.get('camera')
        if keyname is not None:
            # The requested camera was specified as a parameter, so poll just that one.
            cam = CameraSource.get(db.Key(keyname))

            # If disabled then stop processing and don't requeue a task.
            if not cam.enabled:
                self.response.out.write("disabled")
                return
            
            lastimg_time = memcache.get("camera{%s}.lastimg_time" % cam.key())
            alerted = memcache.get("camera{%s}.eventkey" % cam.key()) is not None

            
            try:
                # Loop until we hit the execution time limit.
                while True:                    
                    loop_start_time = datetime.now()
                    
                    # Time will expire at 30 seconds, so stop if we're getting close.
                    if (loop_start_time - proc_start_time) > timedelta(seconds=MODETECT_RUNTIME_LIMIT):
                        break
                
                    if lastimg_time is None:
                        sleep_amt = 0.0
                    elif alerted:
                        td = (loop_start_time - lastimg_time)
                        elapsed = (td.microseconds + (td.seconds + td.days * 24 * 3600) * 10**6) / 10**6
                        sleep_amt = 1.0 / cam.alert_max_fps - elapsed
                    else:
                        td = (loop_start_time - lastimg_time)
                        elapsed = (td.microseconds + (td.seconds + td.days * 24 * 3600) * 10**6) / 10**6
                        sleep_amt = 1.0 / cam.poll_max_fps - elapsed

                    if (sleep_amt > 0.0):
                        time.sleep(sleep_amt)
                        
                    lastimg_time = datetime.now()
                    alerted = self.pollCamera(cam)

            except DeadlineExceededError, apiproxy_errors.DeadlineExceededError:
                self.response.out.write("timeout hit, ignoring")

                
            # Time probably expired, so queue a task to start back up.            
            # This may fail with CancelledError if we don't have time to do that.
            ImageFetcherTask.queueTask(cam.key())
                    
        self.response.out.write("done")

    
    # This is the debugging method used to capture one image from all cameras.
    def get(self):
        self.response.headers['Content-Type'] = 'text/plain'

        # Iterate through all cameras.
        q = CameraSource.all()
        q.filter("enabled =",True).filter("deleted =",False)

        # capturing an image and detect motion once for all cameras.
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
        cam_authuser = self.request.get('authuser')
        cam_authpass = self.request.get('authpass')

        cam = CameraSource(name=cam_name,
                           url=cam_url,
                           poll_max_fps = cam_poll_max_fps,
                           alert_max_fps = cam_alert_max_fps,
                           creation_time = datetime.now(),
                           enabled = cam_enabled,
                           deleted = False,
                           num_secs_after = cam_num_secs_after,
                           authuser = cam_authuser,
                           authpass = cam_authpass)
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
            # TODO: need to escape unsafe characters
            self.response.out.write(' "key": "%s",' % cam.key())
            self.response.out.write(' "name": "%s",' % cam.name)
            self.response.out.write(' "url": "%s",' % cam.url)
            self.response.out.write(' "enabled": %d,' % cam.enabled)
            self.response.out.write(' "poll_max_fps": %d,' % cam.poll_max_fps)
            self.response.out.write(' "alert_max_fps": %d,' % cam.alert_max_fps)
            self.response.out.write(' "num_secs_after": %f,' % cam.num_secs_after)
            
            if cam.authuser:
                tmpuser = cam.authuser
            else:
                tmpuser = ""
                
            if cam.authpass:
                maskedpass = "*" * len(cam.authpass)
            else:
                maskedpass = ""
                
            self.response.out.write(' "authuser": "%s",' % tmpuser)
            self.response.out.write(' "authpass": "%s"' % maskedpass)
            self.response.out.write("}")

        elif cmd == 'save':
            cam.name = self.request.get('name')
            cam.url = self.request.get('url')
            cam.enabled = bool(int(self.request.get('enabled')))
            cam.poll_max_fps = int(self.request.get('poll_max_fps'))
            cam.alert_max_fps = int(self.request.get('alert_max_fps'))
            cam.num_secs_after = float(self.request.get('num_secs_after'))
            
            tmpuser = self.request.get('authuser')
            if not tmpuser:
                cam.authuser = None
            else:
                cam.authuser = tmpuser
                
            tmppass = self.request.get('authpass')
            if not tmppass:
                cam.authpass = None
            elif tmppass != '*' * len(tmppass):
                cam.authpass = tmppass
                
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
                        numDeleted += 1
                else:
                    # No more frames belonging to this event, so delete it too.
                    event.delete()
                    numDeleted += 1

            # Look for CameraSources marked deleted
            q = CameraSource.all()
            q.filter("deleted =",True)
            for cam in q.fetch(10):
                # Mark any events belonging to this deleted CameraSource as deleted.
                q2 = CameraEvent.all()
                q2.filter("camera_id =", cam.key()).filter("deleted =",False)
                for event in q2.fetch(100):
                    numDeleted += 1
                    event.deleted = True
                    event.put()

                # Delete any frames belonging to this deleted CameraSource.
                q2 = CameraFrame.all()
                q2.filter("camera_id =", cam.key())
                for frame in q2.fetch(500):
                    frame.delete()
                    numDeleted += 1

                # Only delete the camera itself if nothing else needed to be done.
                if numDeleted == 0:
                    cam.delete()
                    numDeleted += 1

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
            q2.filter("event_start >=", datetime.now().replace(hour=0,minute=0,second=0))
        elif period == "yesterday":
            q2.filter("event_start >=", datetime.now().replace(hour=0,minute=0,second=0) - timedelta(days=1))
            q2.filter("event_start <", datetime.now().replace(hour=0,minute=0,second=0))
        elif period == "week":
            startofweek = datetime.now().replace(hour=0, minute=0, second=0) - timedelta(days=datetime.now().isoweekday() % 7)
            q2.filter("event_start >=", startofweek)
        elif period == "month":
            #startofmonth = datetime.now().replace(day=1,hour=0,minute=0,second=0)
            startofmonth = datetime.now().replace(hour=0, minute=0, second=0) - timedelta(days=30)
            q2.filter("event_start >=", startofmonth)
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
        startofweek = datetime.now().replace(hour=0, minute=0, second=0) - timedelta(days=datetime.now().isoweekday() % 7)
        #startofmonth = datetime.now().replace(day=1,hour=0,minute=0,second=0)
        startofmonth = datetime.now().replace(hour=0, minute=0, second=0) - timedelta(days=30)
        startofyesterday = datetime.now().replace(hour=0,minute=0,second=0) - timedelta(days=1)
        startoftoday = datetime.now().replace(hour=0,minute=0,second=0)

        for cam in results:

            # Generate a human-readable status indicator.
            # TODO: cam.enabled, cam.last_poll_time, cam.last_poll_result
            if not cam.enabled:
                cam.status_text = "Disabled"
            else:
                lastimg_time = memcache.get("camera{%s}.lastimg_time" % cam.key())
                if lastimg_time is None:
                    cam.status_text = "Enabled, but not recently polled"
                else:
                    td = (datetime.now() - lastimg_time)
                    cam.status_text = "Enabled, polled %s ago" % td


            # TODO: deleted frames should not be included in these counts.
            qf = CameraFrame.all(keys_only=True)
            qf.filter("camera_id =", cam.key())
            qe = CameraEvent.all(keys_only=True)
            qe.filter("deleted =", False).filter("camera_id =", cam.key())
            cam.ftotal = qf.count()
            cam.etotal = qe.count()

            qf.filter("image_time >=", startofmonth)
            qe.filter("event_start >=", startofmonth)
            cam.fthismonth = qf.count()
            cam.ethismonth = qe.count()

            qf.filter("image_time >=", startofweek)
            qe.filter("event_start >=", startofweek)
            cam.fthisweek = qf.count()
            cam.ethisweek = qe.count()

            qf.filter("image_time >=", startoftoday)
            qe.filter("event_start >=", startoftoday)
            cam.ftoday = qf.count()
            cam.etoday = qe.count()

            qf = CameraFrame.all(keys_only=True)
            qf.filter("camera_id =", cam.key())
            qf.filter("image_time >=", startofyesterday)
            qf.filter("image_time <", startoftoday)
            qe = CameraEvent.all(keys_only=True)
            qe.filter("deleted =", False)
            qe.filter("camera_id =", cam.key())
            qe.filter("event_start >=", startofyesterday)
            qe.filter("event_start <", startoftoday)
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

# Displays all of the frames belonging to an event, in an animated playback loop.
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
            
        if not event.viewed:
            event.viewed = True
            event.put()

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

# Send back a scaled thumbnail of any single CameraFrame.
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
        self.redirect("/summary")
        self.response.out.write('<p>Hello cows!</p>')
        self.response.out.write('<a href="/summary">View the system</a>')


