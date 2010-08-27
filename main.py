#!/usr/bin/env python

from google.appengine.ext import webapp
from google.appengine.ext.webapp import util

import handlers




def main():
    application = webapp.WSGIApplication([('/', handlers.MainHandler),
                                          ('/summary', handlers.MainSummaryHandler),
                                          ('/camera/add', handlers.AddCameraSourceHandler),
                                          ('/camera/edit', handlers.EditCameraSourceHandler),
                                          ('/camera/delete', handlers.DeleteCameraSourceHandler),
                                          ('/camera/trigger', handlers.TriggerCameraSourceHandler), 
                                          ('/camera/livethumb', handlers.LiveThumbHandler),
                                          ('/events/browse', handlers.BrowseEventsHandler),
                                          ('/frame/viewthumb', handlers.CameraFrameThumbHandler),
                                          #('/events/delete', handlers.DeleteEventHandler),
                                          #('/events/archive', handlers.ArchiveEventHandler),
                                          #('/events/mjpeg', handlers.GetMjpegEventHandler),
                                          ('/events/imgseq', handlers.GetImgSeqEventHandler),
                                          ('/tasks/poll_sources', handlers.ImageFetcherTask),
                                          ('/tasks/garbage_collector', handlers.GarbageCollectorTask) ],
                                         debug=True)
    util.run_wsgi_app(application)


if __name__ == '__main__':
    main()
