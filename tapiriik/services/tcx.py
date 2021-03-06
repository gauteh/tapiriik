from lxml import etree, objectify
from pytz import UTC
import copy
import dateutil.parser
from datetime import datetime
from .interchange import WaypointType, Activity, ActivityType, Waypoint, Location


class TCXIO:
    Namespaces = {
        None: "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2",
        "ns2": "http://www.garmin.com/xmlschemas/UserProfile/v2",
        "tpx": "http://www.garmin.com/xmlschemas/ActivityExtension/v2",
        "ns4": "http://www.garmin.com/xmlschemas/ProfileExtension/v1",
        "ns5": "http://www.garmin.com/xmlschemas/ActivityGoals/v1",
        "xsi": "http://www.w3.org/2001/XMLSchema-instance"
    }

    def Parse(tcxData, act=None):
        ns = copy.deepcopy(TCXIO.Namespaces)
        ns["tcx"] = ns[None]
        del ns[None]

        act = act if act else Activity()
        act.Distance = None

        try:
            root = etree.XML(tcxData)
        except:
            root = etree.fromstring(tcxData)


        xacts = root.find("tcx:Activities", namespaces=ns)
        if xacts is None:
            raise ValueError("No activities element in TCX")

        xact = xacts.find("tcx:Activity", namespaces=ns)
        if xact is None:
            raise ValueError("No activity element in TCX")

        if not act.Type:
            if xact.attrib["Sport"] == "Biking":
                act.Type = ActivityType.Cycling
            elif xact.attrib["Sport"] == "Running":
                act.Type = ActivityType.Running

        xlaps = xact.findall("tcx:Lap", namespaces=ns)
        startTime = None
        endTime = None

        beginSeg = False
        for xlap in xlaps:
            beginSeg = True
            xtrkseg = xlap.find("tcx:Track", namespaces=ns)
            if xtrkseg is None:
                # Some TCX files have laps with no track - not sure if it's valid or not.
                continue
            for xtrkpt in xtrkseg.findall("tcx:Trackpoint", namespaces=ns):
                wp = Waypoint()
                if len(act.Waypoints) == 0:
                    wp.Type = WaypointType.Start
                elif beginSeg:
                    wp.Type = WaypointType.Lap
                beginSeg = False

                wp.Timestamp = dateutil.parser.parse(xtrkpt.find("tcx:Time", namespaces=ns).text)
                wp.Timestamp.replace(tzinfo=UTC)
                if startTime is None or wp.Timestamp < startTime:
                    startTime = wp.Timestamp
                if endTime is None or wp.Timestamp > endTime:
                    endTime = wp.Timestamp
                xpos = xtrkpt.find("tcx:Position", namespaces=ns)
                if xpos is not None:
                    wp.Location = Location(float(xpos.find("tcx:LatitudeDegrees", namespaces=ns).text), float(xpos.find("tcx:LongitudeDegrees", namespaces=ns).text), None)
                eleEl = xtrkpt.find("tcx:AltitudeMeters", namespaces=ns)
                if eleEl is not None:
                    wp.Location = wp.Location if wp.Location else Location(None, None, None)
                    wp.Location.Altitude = float(eleEl.text)
                hrEl = xtrkpt.find("tcx:HeartRateBpm", namespaces=ns)
                if hrEl is not None:
                    wp.HR = int(hrEl.find("tcx:Value", namespaces=ns).text)
                cadEl = xtrkpt.find("tcx:Cadence", namespaces=ns)
                if cadEl is not None:
                    wp.Cadence = int(cadEl.text)
                extsEl = xtrkpt.find("tcx:Extensions", namespaces=ns)
                if extsEl is not None:
                    tpxEl = extsEl.find("tpx:TPX", namespaces=ns)
                    if tpxEl is not None:
                        powerEl = tpxEl.find("tpx:Watts", namespaces=ns)
                        if powerEl is not None:
                            wp.Power = float(powerEl.text)
                act.Waypoints.append(wp)
                xtrkpt.clear()
                del xtrkpt
        if not len(act.Waypoints):
            raise ValueError("No waypoints in TCX")

        act.Waypoints[len(act.Waypoints)-1].Type = WaypointType.End
        act.TZ = act.Waypoints[0].Timestamp.tzinfo
        act.StartTime = startTime
        act.EndTime = endTime
        act.CalculateDistance()
        act.CalculateUID()
        return act

    def Dump(activity):

        TRKPTEXT = "{%s}" % TCXIO.Namespaces["tpx"]
        root = etree.Element("TrainingCenterDatabase", nsmap=TCXIO.Namespaces)
        activities = etree.SubElement(root, "Activities")
        act = etree.SubElement(activities, "Activity")


        author = etree.SubElement(root, "Author")
        author.attrib["{" + TCXIO.Namespaces["xsi"] + "}type"] = "Application_t"
        etree.SubElement(author, "Name").text = "tapiriik"
        build = etree.SubElement(author, "Build")
        version = etree.SubElement(build, "Version")
        etree.SubElement(version, "VersionMajor").text = "0"
        etree.SubElement(version, "VersionMinor").text = "0"
        etree.SubElement(version, "BuildMajor").text = "0"
        etree.SubElement(version, "BuildMinor").text = "0"
        etree.SubElement(author, "LangID").text = "en"
        etree.SubElement(author, "PartNumber").text = "000-00000-00"

        dateFormat = "%Y-%m-%dT%H:%M:%S.000Z"

        if activity.Name is not None:
            etree.SubElement(act, "Notes").text = activity.Name

        if activity.Type == ActivityType.Cycling:
            act.attrib["Sport"] = "Biking"
        elif activity.Type == ActivityType.Running:
            act.attrib["Sport"] = "Running"
        else:
            act.attrib["Sport"] = "Other"

        etree.SubElement(act, "Id").text = activity.StartTime.astimezone(UTC).strftime(dateFormat)
        lap = track = None
        inPause = False
        lapStartWpt = None
        def newLap(wpt):
            nonlocal lapStartWpt, lap, track
            lapStartWpt = wpt
            lap = etree.SubElement(act, "Lap")
            lap.attrib["StartTime"] = wpt.Timestamp.astimezone(UTC).strftime(dateFormat)
            if wpt.Calories and lapStartWpt.Calories:
                etree.SubElement(lap, "Calories").text = str(wpt.Calories - lapStartWpt.Calories)
            else:
                etree.SubElement(lap, "Calories").text = "0"  # meh schema is meh
            etree.SubElement(lap, "Intensity").text = "Active"
            etree.SubElement(lap, "TriggerMethod").text = "Manual"  # I assume!

            track = etree.SubElement(lap, "Track")

        def finishLap(wpt):
            nonlocal lapStartWpt, lap
            dist = activity.GetDistance(lapStartWpt, wpt)
            xdist = etree.SubElement(lap, "DistanceMeters")
            xdist.text = str(dist)
            # I think this is actually supposed to be "unpaused time" - oh well, no way to really tell that.
            totaltime = etree.SubElement(lap, "TotalTimeSeconds")
            totaltime.text = str((wpt.Timestamp - lapStartWpt.Timestamp).total_seconds())
            lap.insert(0, xdist)
            lap.insert(0, totaltime)

        newLap(activity.Waypoints[0])
        for wp in activity.Waypoints:
            if wp.Location is None or wp.Location.Latitude is None or wp.Location.Longitude is None:
                continue  # drop the point
            if wp.Type == WaypointType.Pause:
                if inPause:
                    continue  # this used to be an exception, but I don't think that was merited
                inPause = True
            if inPause and wp.Type != WaypointType.Pause or wp.Type == WaypointType.Lap:
                # Make a new lap when they unpause
                inPause = False
                finishLap(wp)
                newLap(wp)
            trkpt = etree.SubElement(track, "Trackpoint")
            if wp.Timestamp.tzinfo is None:
                raise ValueError("TCX export requires TZ info")
            etree.SubElement(trkpt, "Time").text = wp.Timestamp.astimezone(UTC).strftime(dateFormat)
            if wp.Location:
                pos = etree.SubElement(trkpt, "Position")
                etree.SubElement(pos, "LatitudeDegrees").text = str(wp.Location.Latitude)
                etree.SubElement(pos, "LongitudeDegrees").text = str(wp.Location.Longitude)

            if wp.Location.Altitude is not None:
                etree.SubElement(trkpt, "AltitudeMeters").text = str(wp.Location.Altitude)
            if wp.HR is not None:
                xhr = etree.SubElement(trkpt, "HeartRateBpm")
                xhr.attrib["{" + TCXIO.Namespaces["xsi"] + "}type"] = "HeartRateInBeatsPerMinute_t"
                etree.SubElement(xhr, "Value").text = str(int(wp.HR))
            if wp.Cadence is not None:
                etree.SubElement(trkpt, "Cadence").text = str(int(wp.Cadence))
            if wp.Power is not None:
                exts = etree.SubElement(trkpt, "Extensions")
                gpxtpxexts = etree.SubElement(exts, "TPX")
                gpxtpxexts.attrib["xmlns"] = "http://www.garmin.com/xmlschemas/ActivityExtension/v2"
                etree.SubElement(gpxtpxexts, "Watts").text = str(int(wp.Power))
        finishLap(wp)
        return etree.tostring(root, pretty_print=True, xml_declaration=True, encoding="UTF-8").decode("UTF-8")
