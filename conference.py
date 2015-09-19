#!/usr/bin/env python

"""
conference.py -- Udacity conference server-side Python App Engine API;
    uses Google Cloud Endpoints

$Id: conference.py,v 1.25 2014/05/24 23:42:19 wesc Exp wesc $

created by wesc on 2014 apr 21

"""

__author__ = 'wesc+api@google.com (Wesley Chun)'

from datetime import datetime

import time

import endpoints
from protorpc import messages
from protorpc import message_types
from protorpc import remote

from google.appengine.api import memcache
from google.appengine.api import taskqueue
from google.appengine.api import urlfetch
from google.appengine.ext import ndb

from models import ConflictException
from models import Profile
from models import ProfileMiniForm
from models import ProfileForm
from models import StringMessage
from models import BooleanMessage
from models import Conference
from models import ConferenceForm
from models import ConferenceForms
from models import ConferenceQueryForm
from models import ConferenceQueryForms
from models import TeeShirtSize
from models import Session
from models import SessionForm
from models import SessionForms
from models import Speaker
from models import SessionTypes

from settings import WEB_CLIENT_ID
from settings import ANDROID_CLIENT_ID
from settings import IOS_CLIENT_ID
from settings import ANDROID_AUDIENCE

from utils import getUserId

EMAIL_SCOPE = endpoints.EMAIL_SCOPE
API_EXPLORER_CLIENT_ID = endpoints.API_EXPLORER_CLIENT_ID
MEMCACHE_ANNOUNCEMENTS_KEY = "RECENT_ANNOUNCEMENTS"
ANNOUNCEMENT_TPL = ('Last chance to attend! The following conferences '
                    'are nearly sold out: %s')
MEMCACHE_FEATURED_SPEAKER_SESSIONS_KEY = "FEATURED_SPEAKER_SESSIONS"
FEATURED_SPEAKER_SESSIONS_TPL=('Check out this session: %s')

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

DEFAULTS = {
    "city": "Default City",
    "maxAttendees": 0,
    "seatsAvailable": 0,
    "topics": ["Default", "Topic"],
}

OPERATORS = {
    'EQ': '=',
    'GT': '>',
    'GTEQ': '>=',
    'LT': '<',
    'LTEQ': '<=',
    'NE': '!='
}

FIELDS = {
    'CITY': 'city',
    'TOPIC': 'topics',
    'MONTH': 'month',
    'MAX_ATTENDEES': 'maxAttendees',
}

CONF_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
)

CONF_POST_REQUEST = endpoints.ResourceContainer(
    ConferenceForm,
    websafeConferenceKey=messages.StringField(1),
)

SESSION_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
    typeOfSession = messages.StringField(2)
)

SESSION_POST_REQUEST = endpoints.ResourceContainer(
    SessionForm,
    websafeConferenceKey=messages.StringField(1),
)

SPEAKER_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    speaker=messages.StringField(1),
)

DURATION_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    duration=messages.StringField(1),
)

USERWISHLIST__POST_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    sessionId=messages.StringField(1),
)

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

@endpoints.api(name='conference', version='v1', audiences=[ANDROID_AUDIENCE],
               allowed_client_ids=[WEB_CLIENT_ID, API_EXPLORER_CLIENT_ID, ANDROID_CLIENT_ID, IOS_CLIENT_ID],
               scopes=[EMAIL_SCOPE])
class ConferenceApi(remote.Service):
    """Conference API v0.1"""

# - - - Conference objects - - - - - - - - - - - - - - - - -

    def _copyConferenceToForm(self, conf, displayName):
        """Copy relevant fields from Conference to ConferenceForm."""
        cf = ConferenceForm()
        for field in cf.all_fields():
            if hasattr(conf, field.name):
                # convert Date to date string; just copy others
                if field.name.endswith('Date'):
                    setattr(cf, field.name, str(getattr(conf, field.name)))
                else:
                    setattr(cf, field.name, getattr(conf, field.name))
            elif field.name == "websafeKey":
                setattr(cf, field.name, conf.key.urlsafe())
        if displayName:
            setattr(cf, 'organizerDisplayName', displayName)
        cf.check_initialized()
        return cf


    def _createConferenceObject(self, request):
        """Create or update Conference object, returning ConferenceForm/request."""
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        #user_id = _getUserId()
        user_id = getUserId(user)

        if not request.name:
            raise endpoints.BadRequestException("Conference 'name' field required")

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        del data['websafeKey']
        del data['organizerDisplayName']

        # add default values for those missing (both data model & outbound Message)
        for df in DEFAULTS:
            if data[df] in (None, []):
                data[df] = DEFAULTS[df]
                setattr(request, df, DEFAULTS[df])

        # convert dates from strings to Date objects; set month based on start_date
        if data['startDate']:
            data['startDate'] = datetime.strptime(data['startDate'][:10], "%Y-%m-%d").date()
            data['month'] = data['startDate'].month
        else:
            data['month'] = 0
        if data['endDate']:
            data['endDate'] = datetime.strptime(data['endDate'][:10], "%Y-%m-%d").date()

        # set seatsAvailable to be same as maxAttendees on creation
        if data["maxAttendees"] > 0:
            data["seatsAvailable"] = data["maxAttendees"]
        # generate Profile Key based on user ID and Conference
        # ID based on Profile key get Conference key from ID
        p_key = ndb.Key(Profile, user_id)
        c_id = Conference.allocate_ids(size=1, parent=p_key)[0]
        c_key = ndb.Key(Conference, c_id, parent=p_key)

        data['key'] = c_key
        data['organizerUserId'] = request.organizerUserId = user_id

        # create Conference, send email to organizer confirming
        # creation of Conference & return (modified) ConferenceForm
        Conference(**data).put()
        taskqueue.add(params={'email': user.email(),
                              'conferenceInfo': repr(request)},
                      url='/tasks/send_confirmation_email'
        )
        return request


    @ndb.transactional()
    def _updateConferenceObject(self, request):
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}

        # update existing conference
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        # check that conference exists
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)

        # check that user is owner
        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException(
                'Only the owner can update the conference.')

        # Not getting all the fields, so don't create a new object; just
        # copy relevant fields from ConferenceForm to Conference object
        for field in request.all_fields():
            data = getattr(request, field.name)
            # only copy fields where we get data
            if data not in (None, []):
                # special handling for dates (convert string to Date)
                if field.name in ('startDate', 'endDate'):
                    data = datetime.strptime(data, "%Y-%m-%d").date()
                    if field.name == 'startDate':
                        conf.month = data.month
                # write to Conference object
                setattr(conf, field.name, data)
        conf.put()
        prof = ndb.Key(Profile, user_id).get()
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))


    @endpoints.method(ConferenceForm, ConferenceForm, path='conference',
                      http_method='POST', name='createConference')
    def createConference(self, request):
        """Create new conference."""
        return self._createConferenceObject(request)


    @endpoints.method(CONF_POST_REQUEST, ConferenceForm,
                      path='conference/{websafeConferenceKey}',
                      http_method='PUT', name='updateConference')
    def updateConference(self, request):
        """Update conference w/provided fields & return w/updated info."""
        return self._updateConferenceObject(request)


    @endpoints.method(CONF_GET_REQUEST, ConferenceForm,
                      path='conference/{websafeConferenceKey}',
                      http_method='GET', name='getConference')
    def getConference(self, request):
        """Return requested conference (by websafeConferenceKey)."""
        # get Conference object from request; bail if not found
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)
        prof = conf.key.parent().get()
        # return ConferenceForm
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
                      path='getConferencesCreated',
                      http_method='POST', name='getConferencesCreated')
    def getConferencesCreated(self, request):
        """Return conferences created by user."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # create ancestor query for all key matches for this user
        confs = Conference.query(ancestor=ndb.Key(Profile, user_id))
        prof = ndb.Key(Profile, user_id).get()
        # return set of ConferenceForm objects per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, getattr(prof, 'displayName')) for conf in confs]
        )


    def _getQuery(self, request):
        """Return formatted query from the submitted filters."""
        q = Conference.query()
        inequality_filter, filters = self._formatFilters(request.filters)

        # If exists, sort on inequality filter first
        if not inequality_filter:
            q = q.order(Conference.name)
        else:
            q = q.order(ndb.GenericProperty(inequality_filter))
            q = q.order(Conference.name)

        for filtr in filters:
            if filtr["field"] in ["month", "maxAttendees"]:
                filtr["value"] = int(filtr["value"])
            formatted_query = ndb.query.FilterNode(filtr["field"], filtr["operator"], filtr["value"])
            q = q.filter(formatted_query)
        return q


    def _formatFilters(self, filters):
        """Parse, check validity and format user supplied filters."""
        formatted_filters = []
        inequality_field = None

        for f in filters:
            filtr = {field.name: getattr(f, field.name) for field in f.all_fields()}

            try:
                filtr["field"] = FIELDS[filtr["field"]]
                filtr["operator"] = OPERATORS[filtr["operator"]]
            except KeyError:
                raise endpoints.BadRequestException("Filter contains invalid field or operator.")

            # Every operation except "=" is an inequality
            if filtr["operator"] != "=":
                # check if inequality operation has been used in previous filters
                # disallow the filter if inequality was performed on a different field before
                # track the field on which the inequality operation is performed
                if inequality_field and inequality_field != filtr["field"]:
                    raise endpoints.BadRequestException("Inequality filter is allowed on only one field.")
                else:
                    inequality_field = filtr["field"]

            formatted_filters.append(filtr)
        return (inequality_field, formatted_filters)


    @endpoints.method(ConferenceQueryForms, ConferenceForms,
                      path='queryConferences',
                      http_method='POST',
                      name='queryConferences')
    def queryConferences(self, request):
        """Query for conferences."""
        conferences = self._getQuery(request)

        # need to fetch organiser displayName from profiles
        # get all keys and use get_multi for speed
        organisers = [(ndb.Key(Profile, conf.organizerUserId)) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return individual ConferenceForm object per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, names[conf.organizerUserId]) for conf in \
                   conferences]
        )


    # - - - Profile objects - - - - - - - - - - - - - - - - - - -

    def _copyProfileToForm(self, prof):
        """Copy relevant fields from Profile to ProfileForm."""
        # copy relevant fields from Profile to ProfileForm
        pf = ProfileForm()
        for field in pf.all_fields():
            if hasattr(prof, field.name):
                # convert t-shirt string to Enum; just copy others
                if field.name == 'teeShirtSize':
                    setattr(pf, field.name, getattr(TeeShirtSize, getattr(prof, field.name)))
                else:
                    setattr(pf, field.name, getattr(prof, field.name))
        pf.check_initialized()
        return pf


    def _getProfileFromUser(self):
        """Return user Profile from datastore, creating new one if non-existent."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        # get Profile from datastore
        user_id = getUserId(user)
        p_key = ndb.Key(Profile, user_id)
        profile = p_key.get()
        # create new Profile if not there
        if not profile:
            profile = Profile(
                key=p_key,
                displayName=user.nickname(),
                mainEmail=user.email(),
                teeShirtSize=str(TeeShirtSize.NOT_SPECIFIED),
            )
            profile.put()

        return profile  # return Profile


    def _doProfile(self, save_request=None):
        """Get user Profile and return to user, possibly updating it first."""
        # get user Profile
        prof = self._getProfileFromUser()

        # if saveProfile(), process user-modifyable fields
        if save_request:
            for field in ('displayName', 'teeShirtSize'):
                if hasattr(save_request, field):
                    val = getattr(save_request, field)
                    if val:
                        setattr(prof, field, str(val))
                        # if field == 'teeShirtSize':
                        # setattr(prof, field, str(val).upper())
                        # else:
                        # setattr(prof, field, val)
                        prof.put()

        # return ProfileForm
        return self._copyProfileToForm(prof)


    @endpoints.method(message_types.VoidMessage, ProfileForm,
                      path='profile', http_method='GET', name='getProfile')
    def getProfile(self, request):
        """Return user profile."""
        return self._doProfile()


    @endpoints.method(ProfileMiniForm, ProfileForm,
                      path='profile', http_method='POST', name='saveProfile')
    def saveProfile(self, request):
        """Update & return user profile."""
        return self._doProfile(request)


    # - - - Announcements - - - - - - - - - - - - - - - - - - - -

    @staticmethod
    def _cacheAnnouncement():
        """Create Announcement & assign to memcache; used by
        memcache cron job & putAnnouncement().
        """
        confs = Conference.query(ndb.AND(
            Conference.seatsAvailable <= 5,
            Conference.seatsAvailable > 0)
        ).fetch(projection=[Conference.name])

        if confs:
            # If there are almost sold out conferences,
            # format announcement and set it in memcache
            announcement = ANNOUNCEMENT_TPL % (
                ', '.join(conf.name for conf in confs))
            memcache.set(MEMCACHE_ANNOUNCEMENTS_KEY, announcement)
        else:
            # If there are no sold out conferences,
            # delete the memcache announcements entry
            announcement = ""
            memcache.delete(MEMCACHE_ANNOUNCEMENTS_KEY)

        return announcement


    @endpoints.method(message_types.VoidMessage, StringMessage,
                      path='conference/announcement/get',
                      http_method='GET', name='getAnnouncement')
    def getAnnouncement(self, request):
        """Return Announcement from memcache."""
        return StringMessage(data=memcache.get(MEMCACHE_ANNOUNCEMENTS_KEY) or "")


    @endpoints.method(message_types.VoidMessage, StringMessage,
                      path='conference/announcement/put',
                      http_method='GET', name='putAnnouncement')
    def putAnnouncement(self, request):
        """Put Announcement into memcache"""
        return StringMessage(data=self._cacheAnnouncement())


    # - - - Registration - - - - - - - - - - - - - - - - - - - -

    @ndb.transactional(xg=True)
    def _conferenceRegistration(self, request, reg=True):
        """Register or unregister user for selected conference."""
        retval = None
        prof = self._getProfileFromUser()  # get user Profile

        # check if conf exists given websafeConfKey
        # get conference; check that it exists
        wsck = request.websafeConferenceKey
        conf = ndb.Key(urlsafe=wsck).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % wsck)

        # register
        if reg:
            # check if user already registered otherwise add
            if wsck in prof.conferenceKeysToAttend:
                raise ConflictException(
                    "You have already registered for this conference")

            # check if seats avail
            if conf.seatsAvailable <= 0:
                raise ConflictException(
                    "There are no seats available.")

            # register user, take away one seat
            prof.conferenceKeysToAttend.append(wsck)
            conf.seatsAvailable -= 1
            retval = True

        # unregister
        else:
            # check if user already registered
            if wsck in prof.conferenceKeysToAttend:

                # unregister user, add back one seat
                prof.conferenceKeysToAttend.remove(wsck)
                conf.seatsAvailable += 1
                retval = True
            else:
                retval = False

        # write things back to the datastore & return
        prof.put()
        conf.put()
        return BooleanMessage(data=retval)


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
                      path='conferences/attending',
                      http_method='GET', name='getConferencesToAttend')
    def getConferencesToAttend(self, request):
        """Get list of conferences that user has registered for."""
        prof = self._getProfileFromUser()  # get user Profile
        conf_keys = [ndb.Key(urlsafe=wsck) for wsck in prof.conferenceKeysToAttend]
        conferences = ndb.get_multi(conf_keys)

        # get organizers
        organisers = [ndb.Key(Profile, conf.organizerUserId) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return set of ConferenceForm objects per Conference
        return ConferenceForms(items=[
            self._copyConferenceToForm(conf, names[conf.organizerUserId]) for conf in conferences
        ])

    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
                      path='conference/{websafeConferenceKey}',
                      http_method='POST', name='registerForConference')
    def registerForConference(self, request):
        """Register user for selected conference."""
        return self._conferenceRegistration(request)


    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
                      path='conference/{websafeConferenceKey}',
                      http_method='DELETE', name='unregisterFromConference')
    def unregisterFromConference(self, request):
        """Unregister user for selected conference."""
        return self._conferenceRegistration(request, reg=False)


# - - - Sessions - - - - - - - - - - - - - - - - - - - -

    def _copySessionToForm(self, session):
        """Copy relevant fields from Session to SessionForm."""
        sf = SessionForm()
        for field in sf.all_fields():

            # For all fields except session_id
            if hasattr(session, field.name):

                # If typeOfSession, assign enum types
                if field.name.endswith('typeOfSession'):
                    setattr(sf, field.name, getattr(SessionTypes, str(getattr(session, field.name))))

                # If date and startTime, convert date and startTime to string
                elif field.name in ['date', 'startTime']:
                    setattr(sf, field.name, str(getattr(session, field.name)))
                else:
                    setattr(sf, field.name, getattr(session, field.name))

            # For the session_id field
            elif field.name == "session_id":
                setattr(sf, field.name, session.key.urlsafe())

        sf.check_initialized()
        return sf


    def _createSessionObject(self, request):
        """Create or update Session object, returning SessionForm/request."""
        # preload necessary data items
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # if name not entered
        if not request.name:
            raise endpoints.BadRequestException("Session 'name' field required")

        # copy SessionForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}

        required_fields = ['name', 'speaker', 'duration', 'typeOfSession', 'date', 'startTime', 'conference_id']
        for field_name in required_fields:
            # check for required fields
            if not data.get(field_name, None):
                raise endpoints.BadRequestException("Session '%s' field required" % field_name)

        # convert date and startTime from string to date and time objects
        if data['date']:
            data['date'] = datetime.strptime(data['date'][:10], "%Y-%m-%d").date()
        if data['startTime']:
            data['startTime'] = datetime.strptime(data['startTime'][:5], "%H:%M").time()

        # store typeOfSession as a string
        data['typeOfSession']=str(data['typeOfSession'])

        # validate conference
        conf = ndb.Key(urlsafe=data.get('conference_id')).get()
        if not conf:
            raise endpoints.BadRequestException("Could not find a conference with id=%s" % data.get('conference_id'))

        # Only the conf owner can create this session
        if user_id != conf.organizerUserId:
            raise endpoints.UnauthorizedException("Only the organizer of the conference can create sessions")
        del data['conference_id']

        # Generate session key
        parent_key = ndb.Key(Conference, conf.key.id())
        session_id = Session.allocate_ids(size=1, parent=parent_key)[0]
        session_key = ndb.Key(Session, session_id, parent=parent_key)
        data['key']=session_key
        data['organizerUserId']=user_id
        del data['session_id']

        Session(**data).put()
        # Add task to task queue for featured speaker announcement
        taskqueue.add(params={'speaker': data['speaker'],
            'wcsk': request.conference_id},
            url='/tasks/set_featured_speaker_session_announcement'
        )
        return request


    @endpoints.method(SessionForm, SessionForm, path='session',
                      http_method='POST', name='createSession')
    def createSession(self, request):
        """Creates a new session for a conference. Open only to the conference organizer"""
        newSession = self._createSessionObject(request)
        return newSession


    @endpoints.method(CONF_GET_REQUEST, SessionForms,
                      path='conference/{websafeConferenceKey}/sessions',
                      http_method='GET', name='getConferenceSessions')
    def getConferenceSessions(self, request):
        """Return all sessions for the conference (by websafeConferenceKey)."""
        # validate conference
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endspoints.NotFoundException('No conference found with key: %s' % request.websafeConferenceKey)

        # get all sessions for this conference
        sessions=Session.query(ancestor=ndb.Key(Conference, conf.key.id()))
        return SessionForms(items=[self._copySessionToForm(sess) for sess in sessions])


    @endpoints.method(SESSION_GET_REQUEST, SessionForms,
                      path='conference/{websafeConferenceKey}/sessions/{typeOfSession}',
                      http_method='GET', name='getConferenceSessionsByType')
    def getConferenceSessionsByType(self, request):
        """Return all sessions for the conference (by websafeConferenceKey) that are of the given session type."""
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        # get session type
        typeOfSession=data['typeOfSession']

        # validate conference
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)

        # Get all sessions for the conference matching the type
        sessions = Session.query(Session.typeOfSession == typeOfSession, ancestor=ndb.Key(Conference, conf.key.id()))
        return SessionForms(items=[self._copySessionToForm(sess) for sess in sessions])


    @endpoints.method(SPEAKER_GET_REQUEST, SessionForms,
                      path='{speaker}/sessions',
                      http_method='GET', name='getSessionsBySpeaker')
    def getSessionsBySpeaker(self, request):
        """Return all sessions across all conferences for the given speaker"""
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        # get speaker
        speaker = data['speaker']

        # Get all sessions for the given speaker
        sessions = Session.query(Session.speaker == speaker)
        return SessionForms(items=[self._copySessionToForm(sess) for sess in sessions])


    @endpoints.method(message_types.VoidMessage, SessionForms,
            http_method='GET', name='getShortSessions')
    def getShortSessions(self, request):
        """Returns sessions that are 3 hours or less"""
        # Find all sessions
        sessions = Session.query(Session.duration <= 180)
        return SessionForms(
            items=[self._copySessionToForm(sess) for sess in sessions]
        )


    @endpoints.method(message_types.VoidMessage, SessionForms,
            http_method='GET', name='getUnknownSessions')
    def getUnknownSessions(self, request):
        """Returns sessions that are initially set as unknown type"""
        # Find all sessions
        sessions = Session.query(Session.typeOfSession == "UNKNOWN")
        return SessionForms(
            items=[self._copySessionToForm(sess) for sess in sessions]
        )


    @endpoints.method(message_types.VoidMessage, SessionForms,
            http_method='GET', name='getNonWorkshopBeforeSevenSessions')
    def getNonWorkshopBeforeSevenSessions(self, request):
        """Returns sessions that are non-workshop and before 7pm"""

        #convert 7pm from string to time object for comparison purposes
        sevenTime =datetime.strptime('19:00', '%H:%M').time()

        # THROWS error : BadRequestError: Only one inequality filter per query is supported. Encountered both typeOfSession and startTime
        # sessions=Session.query(ndb.AND(Session.typeOfSession!= "WORKSHOP", Session.startTime < sevenTime))

        # SOLUTION to query sessions that are not workshops and start before 7pm
        sessions = Session.query(ndb.AND(Session.startTime < sevenTime,
            ndb.OR(
                Session.typeOfSession == "UNKNOWN",
                Session.typeOfSession == "KEYNOTE",
                Session.typeOfSession == "LECTURE")))
        return SessionForms(
            items=[self._copySessionToForm(sess) for sess in sessions]
        )


    @endpoints.method(USERWISHLIST__POST_REQUEST, SessionForm,
            http_method='POST', name='addSessionToWishlist')
    def addSessionToWishlist(self, request):
        """Saves a session to a user's wishlist"""
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        # get the session
        session = ndb.Key(urlsafe=request.sessionId).get()

        # validate session
        if not session:
            raise endpoints.NotFoundException(
                'No session found with key: %s' % request.sessionId)

        # get the user profile
        profile = self._getProfileFromUser()

        # check if session is already in wishlist
        if session.key in profile.sessionsInWishList:
            raise endpoints.BadRequestException(
                'Session already saved to wishlist: %s' % request.sessionId)

        # add to user profile's wishlist
        profile.sessionsInWishList.append(session.key)
        profile.put()
        return self._copySessionToForm(session)


    @endpoints.method(message_types.VoidMessage, SessionForms,
            http_method='POST', name='getSessionsInWishlist')
    def getSessionsInWishlist(self, request):
        """Returns a user's wishlist of sessions"""
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        # get user's profile and wishlist and sessions in wishlist
        profile = self._getProfileFromUser()
        wishlist = profile.sessionsInWishList
        sessions = [session_key.get() for session_key in wishlist]
        return SessionForms(
            items=[self._copySessionToForm(sess) for sess in sessions]
        )


    # Get announcement for the featured speaker sessions
    @staticmethod
    def _cacheFeaturedSpeakerSessions(speaker, wsck):
        """
            When a new session is added to a conference, check the speaker.
            If there is more than one session by this speaker at this conference,
            also add a new Memcache entry that features the speaker and session names.
            You can choose the Memcache key.
        """
        # Validate conference
        conf = ndb.Key(urlsafe=wsck).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % wsck)
        parent_key = ndb.Key(Conference, conf.key.id())

        # Get all sessions of this conference by this speaker
        sessions=Session.query(ancestor=parent_key).filter(Session.speaker==speaker).fetch(projection=[Session.name])

        # Check the count of sessions for this speaker
        session_count = len(sessions)

        # If count is greater than 1 then this speaker is in more than 1 session so store in memcache
        if session_count > 1:
            featured_speaker='%s with sessions: %s' % (speaker, (', '.join(sess.name for sess in sessions)))
            memcache.set(MEMCACHE_FEATURED_SPEAKER_SESSIONS_KEY, featured_speaker)


    @endpoints.method(message_types.VoidMessage, StringMessage,
                      path='session/featured/get',
                      http_method='GET', name='getFeaturedSpeaker')
    def getFeaturedSpeaker(self, request):
        """Return Featured Speaker and Sessions from memcache."""
        return StringMessage(data=memcache.get(MEMCACHE_FEATURED_SPEAKER_SESSIONS_KEY) or "TBD")


api = endpoints.api_server([ConferenceApi])  # register API
