#!/usr/bin/env python

from google.appengine.ext import db


class CameraSource(db.Model):
    name = db.StringProperty(required=True)
    url = db.LinkProperty(required=True)
    authuser = db.StringProperty()
    authpass = db.StringProperty()
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
    # TODO: last_poll_time, last_poll_result


class CameraEvent(db.Model):
    camera_id = db.ReferenceProperty(CameraSource, required=True)
    event_start = db.DateTimeProperty(required=True)
    event_end = db.DateTimeProperty(required=True)
    max_motion_rating = db.RatingProperty()
    avg_motion_rating = db.RatingProperty()
    total_motion_rating = db.IntegerProperty(default=0)
    total_frames = db.IntegerProperty(default=0,required=True)
    alarm_frames = db.IntegerProperty(default=0)
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
    #full_size_blobref = blobstore.BlobReferenceProperty(required=False)
    motion_rating = db.RatingProperty()
    alarmed = db.BooleanProperty(default=False)

