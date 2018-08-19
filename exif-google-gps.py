#!/usr/bin/env python


import argparse
import json
import logging
import os.path
import pickle
import sys
import time
from datetime import (
    datetime,
    timedelta
)
from fractions import Fraction


import piexif


LOG_FMT = '[%(levelname)s] %(message)s'


class JpegFile():
    def __init__(self, path, logger=None):
        self.path = path
        self._ts = None
        self._exif = None

    @property
    def timestamp(self):
        if self._ts is None:
            self._ts = self._get_timestmap()

        return self._ts

    @property
    def exif(self):
        if self._exif is None:
            self._exif = piexif.load(self.path)

        return self._exif

    @property
    def has_geo(self):
        try:
            return (
                piexif.GPSIFD.GPSLatitude in self.exif['GPS'] or
                piexif.GPSIFD.GPSLongitude in self.exif['GPS'])
        except KeyError:
            return False

    def write_lat_lng(self, lat, lng):
        # Some code copied from:
        # https://gist.github.com/c060604/8a51f8999be12fc2be498e9ca56adc72

        def to_deg(value, loc):
            """convert decimal coordinates into degrees, munutes and seconds tuple
            Keyword arguments: value is float gps-value, loc is direction list["S", "N"] or ["W", "E"]
            return: tuple like (25, 13, 48.343 ,'N')
            """
            if value < 0:
                loc_value = loc[0]
            elif value > 0:
                loc_value = loc[1]
            else:
                loc_value = ""
            abs_value = abs(value)
            deg = int(abs_value)
            t1 = (abs_value - deg) * 60
            min = int(t1)
            sec = round((t1 - min) * 60, 5)
            return (deg, min, sec, loc_value)

        def change_to_rational(number):
            """convert a number to rational
            Keyword arguments: number
            return: tuple like (1, 2), (numerator, denominator)
            """
            f = Fraction(str(number))
            return (f.numerator, f.denominator)

        lat_deg = to_deg(lat, ["S", "N"])
        lng_deg = to_deg(lng, ["W", "E"])

        exiv_lat = (
            change_to_rational(lat_deg[0]),
            change_to_rational(lat_deg[1]),
            change_to_rational(lat_deg[2]))
        exiv_lng = (
            change_to_rational(lng_deg[0]),
            change_to_rational(lng_deg[1]),
            change_to_rational(lng_deg[2]))

        self.exif['GPS'] = {
            piexif.GPSIFD.GPSVersionID: (2, 0, 0, 0),
            # piexif.GPSIFD.GPSAltitudeRef: 1,
            # piexif.GPSIFD.GPSAltitude: change_to_rational(round(altitude)),
            piexif.GPSIFD.GPSLatitudeRef: lat_deg[3],
            piexif.GPSIFD.GPSLatitude: exiv_lat,
            piexif.GPSIFD.GPSLongitudeRef: lng_deg[3],
            piexif.GPSIFD.GPSLongitude: exiv_lng,
        }

        piexif.insert(piexif.dump(self.exif), self.path)

    def _get_timestmap(self):
        # Code ripped from ocdutils: http://github.com/ldotlopez/ocdutils
        t = {
            'original': (piexif.ExifIFD.DateTimeOriginal,
                         piexif.ExifIFD.OffsetTimeOriginal),
            'digitized': (piexif.ExifIFD.DateTimeDigitized,
                          piexif.ExifIFD.OffsetTimeDigitized),
        }

        for (key, (dt_tag, offset_tag)) in t.items():
            try:
                dt = self.exif['Exif'][dt_tag].decode('ascii')
            except KeyError:
                t[key] = None
                continue

            delta = timedelta()

            if dt.find(' 24:') > 0:
                dt = dt.replace(' 24:', ' 00:')
                delta = datetime.timedelta(days=1)

            dt = datetime.strptime(dt, '%Y:%m:%d %H:%M:%S')
            dt = dt + delta

            t[key] = dt

        if not any(t.values()):
            msg = "exif tags not found"
            raise ValueError(msg)

        if all(t.values()) and t['original'] != t['digitized']:
            msg = "original:{original} != digitized:{digitized}"
            msg = msg.format(original=t['original'], digitized=t['digitized'])
            raise ValueError(msg)

        dt = t['digitized'] or t['original']
        return int(time.mktime(dt.timetuple()))

    def __unicode__(self):
        return self.path

    def __repr__(self):
        return str.__repr__(self.path)


class GeoData():
    def __init__(self, locations_json=None):
        name, _ = os.path.splitext(locations_json)
        dump_file = name + '.bin'

        try:
            with open(dump_file, 'rb') as fh:
                self._d = pickle.load(fh)

        except IOError:
            self._d = set()

            with open(locations_json, 'r', encoding='utf-8') as fh:
                for location in json.load(fh)['locations']:
                    self.save(location)

            with open(dump_file, 'wb+') as fh:
                fh.write(self.dump())

    def save(self, location):
        self._d.add(
            (int(location['timestampMs']) / 1000,
             location['latitudeE7'] / 10000000,
             location['longitudeE7'] / 10000000)
        )

    def search(self, ts, max_delta):
        def check_bounds(ts_):
            return True

        if len(self._d) < 2:
            raise ValueError('No data available')

        for idx in range(0, len(self._d) - 1):
            delta_prev = self._d[idx][0] - ts
            delta_post = self._d[idx+1][0] - ts

            if not (delta_prev <= 0 and delta_post >= 0):
                continue

            if delta_prev > max_delta and delta_post > max_delta:
                raise ValueError('Max delta')

            if abs(delta_prev) < abs(delta_post):
                return self._d[idx][1], self._d[idx][2]
            else:
                return self._d[idx+1][1], self._d[idx+1][2]

        raise ValueError(ts)

    def compile(self):
        if not isinstance(self._d, list):
            self._d = sorted(self._d)

    def dump(self):
        self.compile()
        return pickle.dumps(self._d)


def main():
    parser = argparse.ArgumentParser(
        description=("Write GPS EXIF tags using data from Google location "
                     "history.\n"
                     "\n"
                     "It's recommended to use the '-n' option before real "
                     "usage in order to check results.\n"
                     "\n"
                     "If the output shows approximate locations but not the "
                     "apropiates (ie. it shows places where you have been "
                     "before or after the picture was taken) try the "
                     "--offset option"))
    parser.add_argument(
        '-g', '--geo',
        required=True,
        help=("Google location history file. "
              "Download from: https://takeout.google.com/"))
    parser.add_argument(
        '-o', '--offset',
        help="Time offset in seconds",
        default='0')
    parser.add_argument(
        '--max-delta',
        default=60*15,  # 15 minutes
        help=("Max desviation between JPG exif datetime and data from "
              "location history file"))
    parser.add_argument(
        '-n', '--dry_run',
        action='store_true',
        help="Don't write GPS data, just print what would be done")
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help="Enable debug messages")
    parser.add_argument(
        'files',
        nargs='+',
        help="JPGs to process")

    args = parser.parse_args(sys.argv[1:])
    args.offset = int(args.offset)

    if args.verbose:
        logging.basicConfig(format=LOG_FMT, level=logging.DEBUG)
    else:
        logging.basicConfig(format=LOG_FMT, level=logging.WARNING)

    geo = GeoData(args.geo)

    for jpg in args.files:
        jpg = JpegFile(jpg)

        lat, lng = geo.search(jpg.timestamp + args.offset,
                              max_delta=args.max_delta)

        if args.dry_run or args.verbose:
            msg = ('[{f}] {dt} ({lat}, {lng}) '
                   'https://www.google.com/maps?z=14&q=loc:{lat},{lng}')
            msg = msg.format(
                dt=datetime.strftime(
                    datetime.fromtimestamp(jpg.timestamp + args.offset), '%c'),
                f=jpg, ts=jpg.timestamp, lat=lat, lng=lng)
            logging.info(msg)

        if not args.dry_run:
            if jpg.has_geo:
                msg = "skipping: '{jpg}' already has GPS data. try --force"
                msg = msg.format(jpg=str(jpg))
                logging.info(msg)
                continue

            jpg.write_lat_lng(lat, lng)


if __name__ == '__main__':
    main()
