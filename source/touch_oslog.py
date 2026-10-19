"""Stream only backboardd's touch records from the iPad as oslog JSON lines, filtered on the device.

`pymobiledevice3 developer dvt oslog` asks the device for every process's log (debug level included) and applies
`--pid` on the PC after decoding each row in pure Python. While touching, that decoding falls minutes behind
(a 1-minute play took ~6 minutes to arrive). This runs the same CLI command, but sets an NSPredicate in the
activity-trace tap's config so the device sends only the touch records we keep.

Copied to viewer/ by source/build_video_sync.py and started by launch_viewer.py:
    python touch_oslog.py [--predicate PRED] [pymobiledevice3 device options, e.g. --udid ...]
"""
import sys

from pymobiledevice3.services.dvt.instruments import activity_trace_tap

# Keys verified against iPadOS 26.6.2 (2026-10-19): subsystem, category and composedMessage filter on the device
PREDICATE = ('subsystem == "com.apple.BackBoard" AND category == "TouchEvents" '
             'AND composedMessage BEGINSWITH "Digitizer token:" AND composedMessage CONTAINS "behavior: foreground"')
READY_MARKER = 'TOUCH_OSLOG_READY'


def main():
    args = sys.argv[1:]
    predicate = PREDICATE
    if '--predicate' in args:
        i = args.index('--predicate')
        predicate = args[i + 1]
        del args[i:i + 2]

    original_init = activity_trace_tap.ActivityTraceTap.__init__

    def init(self, dvt, enable_http_archive_logging=False):
        original_init(self, dvt, enable_http_archive_logging)
        self._config['predicate'] = predicate

    activity_trace_tap.ActivityTraceTap.__init__ = init

    # With the device-side filter nothing arrives until a touch, so announce when the stream is open
    original_aenter = activity_trace_tap.ActivityTraceTap.__aenter__

    async def aenter(self):
        tap = await original_aenter(self)
        print(READY_MARKER, file=sys.stderr, flush=True)
        return tap

    activity_trace_tap.ActivityTraceTap.__aenter__ = aenter

    from pymobiledevice3.__main__ import main as pymobiledevice3_main
    sys.argv = ['pymobiledevice3', 'developer', 'dvt', 'oslog', '--format', 'json', *args]
    sys.exit(pymobiledevice3_main())


if __name__ == '__main__':
    main()
