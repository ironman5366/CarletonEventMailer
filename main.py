# Builtin imports
import logging
import datetime
import json
import sys
import os

# External imports
import requests
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from pytz import timezone
import dateutil.parser

# Logging
FORMAT = '%(asctime)-15s %(user)-8s %(message)s'
logging.basicConfig(filename="events.log", format=FORMAT)
log = logging.getLogger('events')

# Define central time using pytz
CST = timezone('US/Central')

path = os.path.dirname(__file__)

def parse_events(ical_data: str):
    """
    Parse out events from raw ical data
    :param ical_data:
    :return events: A list of events happening today
    """
    now = datetime.datetime.now(tz=CST)
    today = now.date()
    lines = iter(ical_data.splitlines())
    events = []
    current_event = {}
    current_line = next(lines, None)
    in_description = False
    while current_line:
        if in_description:
            # Check to see if this line is trying to break out of a description
            if ":" in current_line and current_line[0].isupper():
                if all([c.isupper() for c in current_line.split(":")[0]]):
                    in_description = False
                    continue
            # If it's not breaking out of the description, continue to append to the description in the current event
            current_event['description'] += current_line
        else:
            if current_line == "END:VEVENT":
                if "start_time" in current_event.keys():
                    events.append(current_event)
                current_event = {}
            elif "DTSTART:" in current_line:
                start_time = dateutil.parser.isoparse(current_line.split("DTSTART:")[1])
                start_time = start_time
                start_date = start_time.date()
                if start_date == today:
                    current_event["start_time"] = start_time
                else:
                    # Skip through the rest of the event if it isn't today
                    current_event = {}
                    while current_line not in [None, "END:VEVENT"]:
                        current_line = next(lines, None)
            elif "SUMMARY:" in current_line:
                current_event["summary"] = current_line.split("SUMMARY:")[1]
            elif "LOCATION:" in current_line:
                current_event["location"] = current_line.split("LOCATION:")[1]
            elif "URL:" in current_line:
                current_event["url"] = current_line.split("URL:")[1]
            elif "DESCRIPTION:" in current_line:
                current_event["description"] = current_line.split("DESCRIPTION:")[1]
                in_description = True
        current_line = next(lines, None)
    log.info(f"Parsed {len(events)} events happening today")
    return events


def get_event_data():
    # Get the date in the format of carleton's ICS url
    now = datetime.datetime.now(tz=CST)
    today = now.date()
    today_str = today.strftime('%Y-%m-%d')
    log.debug(f"ICS date is {today_str}")
    endpoint = f"https://apps.carleton.edu/calendar/?start_date={today_str}&format=ical"
    log.info("Downloading ICS data...")
    rep = requests.get(endpoint)
    ics_content = rep.content.decode('utf-8')
    events = parse_events(ics_content)
    return events

def email_subscribers(subscribers, sg: SendGridAPIClient, events):
    now = datetime.datetime.now(tz=CST)
    date_str = now.strftime('%A, %B %d, %Y')
    event_template = '''
    <th class="column-empty" width="30"style="font-size:0pt; line-height:0pt; padding:0; margin:0; font-weight:normal; direction:ltr;"></th>
        <th class="column-top" width="280"style="font-size:0pt; line-height:0pt; padding:0; margin:0; font-weight:normal; vertical-align:top;">
            <table width="100%" border="0" cellspacing="0" cellpadding="0">
                <tr>
                    <td valign="top">
                        <table width="100%" border="0" cellspacing="0" cellpadding="0">
                            <tr>
                                <td class="h5-black black"style="font-family:'Raleway', Arial,sans-serif; font-size:14px; line-height:18px; text-align:left; padding-bottom:15px; text-transform:uppercase; font-weight:bold; color:#000000;"><multiline>{headline}</multiline></td>
                            </tr>
                            <tr>
                                <td class="text grey pb10"style="font-family:'Raleway', Arial,sans-serif; font-size:14px; line-height:22px; text-align:left; color:#a1a1a1; padding-bottom:10px;"><multiline>{time}</multiline></td>
                            </tr>
                            <tr>
                                <td class="text"style="color:#5d5c5c; font-family:'Raleway', Arial,sans-serif; font-size:14px; line-height:22px; text-align:left;"><multiline>{description}<br></br><strong>{location}</strong></multiline></td>
                            </tr>
                            <tr>
                                <td class="text-button3"style="color:#000000; font-family:'Kreon', 'Times New Roman', Georgia, serif; font-size:15px; line-height:15px; text-align:center; border:1px solid #000000; padding:7px 20px; border-radius:15px;"><multiline><a href="{url}" target="_blank" class="link"style="color:#000001; text-decoration:none;"><span class="link"style="color:#000001; text-decoration:none;">Details</span></a></multiline></td>
                            </tr>
                        </table>
                    </td>
                    <td class="img" valign="top" width="15"style="font-size:0pt; line-height:0pt; text-align:left;"></td>
                </tr>
            </table>
        </th>'''
    event_str = ''
    for event in events:
        event_time_str = event["start_time"].strftime("%I:%M %p")
        if "url" in event.keys():
            event_url = event["url"]
        else:
            event_url = "https://apps.carleton.edu/calendar/?view=daily"
        if "location" in event.keys():
            event_location = event["location"]
        else:
            event_location = ''
        if "description" in event.keys():
            event_description = event["description"]
        else:
            event_description = "No description found"
        event_str += event_template.format(headline=event["summary"], time=event_time_str, description=event_description,
                                     location=event_location, url=event_url)
    template = open(f'{path}/regular/mailbakery-omicron-regular.html', encoding='utf-8').read()
    content = template.format(
        date=date_str,
        events=event_str
    )
    message = Mail(
        from_email='The Daily Will <no-reply@willbeddow.com>',
        to_emails=subscribers,
        subject='The Daily Will',
        html_content=content)
    sg.send(message)


def main():
    log.info("Starting logger")
    # Load subscribers from JSON file
    try:
        subscribers_data = json.load(open(f"{path}/subscribers.json", encoding="utf-8"))
        subscribers = subscribers_data["subscribers"]
        sendgrid_key = subscribers_data["sendgrid_api"]
        # Check to make sure all subscribers look like emails
        assert type(subscribers) == list
        assert (all(["@" in e for e in subscribers]))
        log.info(f"Loaded {len(subscribers)} subscribers")
        # Download Carleton's event data
        events = get_event_data()
        log.info("Downloaded event data")
        # Start the Sendgrid API Client
        sg = SendGridAPIClient(sendgrid_key)
        log.info("Initialized sendgrid")
        email_subscribers(subscribers, sg, events)
    except (FileNotFoundError, json.JSONDecodeError, KeyError, AssertionError):
        log.error("Couldn't load subscribers file, exiting")
        sys.exit(1)


if __name__ == "__main__":
    main()
