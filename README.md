App Engine application for the Udacity training course.

## Products
- [App Engine][1]

## Language
- [Python][2]

## APIs
- [Google Cloud Endpoints][3]

## Setup Instructions
1. Update the value of `application` in `app.yaml` to the app ID you
   have registered in the App Engine admin console and would like to use to host
   your instance of this sample.
1. Update the values at the top of `settings.py` to
   reflect the respective client IDs you have registered in the
   [Developer Console][4].
1. Update the value of CLIENT_ID in `static/js/app.js` to the Web client ID
1. (Optional) Mark the configuration files as unchanged as follows:
   `$ git update-index --assume-unchanged app.yaml settings.py static/js/app.js`
1. Run the app with the devserver using `dev_appserver.py DIR`, and ensure it's running by visiting your local server's address (by default [localhost:8080][5].)
1. (Optional) Generate your client library(ies) with [the endpoints tool][6].
1. Deploy your application.


[1]: https://developers.google.com/appengine
[2]: http://python.org
[3]: https://developers.google.com/appengine/docs/python/endpoints/
[4]: https://console.developers.google.com/
[5]: https://localhost:8080/
[6]: https://developers.google.com/appengine/docs/python/endpoints/endpoints_tool


## Running deployed application for Project 4 Udacity Fullstack Web Developer Nanodegree

1. For frontend application go to https://conference-app-1039.appspot.com/#/
1. For apis explorer go to https://apis-explorer.appspot.com/apis-explorer/?base=https://conference-app-1039.appspot.com/_ah/api#p/

## Explanation for Tasks 1-4

- [Task 1: Add Sessions to a Conference]

  We were tasked with adding sessions to a conference and a speaker for a session and be able to get a conference's sessions by the conference id and also session type.

  Explanations are as follows:

  For my implementations of the Session and Speaker, I used the existing Conference entity as samples to follow in order to create a consistently designed application. I decided to implement the Speaker as its own entity thinking this would be better for storing more properties about the speaker of a conference such as occupation, company, hometown, etc. for the future. For the Session, I defined Session, SessionForm, and SessionForms entities. Based on the requirements, Session and Conference share a child/parent relationship such that a Conference can have multiple sessions but a Session belongs to a single Conference. The Session entity properties are all stored as basic string types with the exception of "typeOfSession" which is an enum defined in SessionTypes. This is modeled after "teeShirtSize" from the Profile entity to restrict the user to select the type of session for a conference session.

  For the endpoint methods, I also used the existing endpoints as samples to follow. For "createSession()", I followed "createConference()" and created static methods,  "createSessionObject" and "copySessionToForm." The "createSession()" method calls "createSessionObject," which actually creates the new Session and returns the request and "copySessionToForm" copies the newly created Session's properties to SessionForm. Inside of "createSessionObject" I made sure to get the conference object that the new session will be created for. The method "getConferenceSessions()" takes in as input a conference key and after validation returns this conference's sessions and getting passed to SessionForms. The method "getConferenceSessionsByType()" is similar to "getConferenceSessions()" but takes in both a conference key and "typeOfSession" as inputs to return all sessions queried by both values and getting passed to SessionForms.

- [Task 2: Add Sessions to User Wishlist]

  We were tasked with adding the capability of allowing users to have a wishlist of sessions that they can use to bookmark sessions and retrieve them.

  Explanations are as follows:

  The purpose of the wishlist is for the user to be able to store and retrieve sessions they are interested in attending. I decided to implement the wishlist by storing it simply as a Key property of the existing Profile entity as "sessionsInWishList" rather than storing it as its own separate entity and making it more complex. Storing it as a Key property allows me to get the full Session object information stored in the wishlist.

  For the endpoint methods, in "addSessionToWishlist()" I decided to allow adding all conference sessions because that seemed more useful to me from a user perspective. This method takes in a "sessionId" as an input to get the Session that is to be stored. It then checks whether the Session is already in the current user's Session wishlist and then passing validation stores the session in the Profile "sessionsInWishList." The "copySessionToForm" method then gets called to copy this Session's properties to SessionForm for display. In "getSessionsInWishlist()" I get the user profile "sessionsInWishList" and return them in SessionForms since there are multiple Sessions to be stored.

- [Task 3: Work on indexes and queries]

  We were tasked to think of two new queries that would be useful for this application, implement them and make sure the indexes support them, and solve a query related problem.  

  Explanations are as follows:

  1. "getShortSessions()" - This query will help the user identify sessions of conferences that are less than or equal to 3 hours(180 minutes) so the user can quickly see which sessions are shorter in length. Maybe a user has some type to attend a conference but wants to be selective on the conference based on session length. Through this query the user can select conference sessions that are not too long and better accomodating to his or her schedule.
  Code: sessions = Session.query(Session.duration <= 180)

  1. "getUnknownSessions()" - This query is for helping conference central application administrators find out which sessions have been created for which the organizer selected "unknown" as the type of session maybe in the rush to organize a session or was not initially sure what type would be appropriate. This information will then help the administrators to fix this and assign a type for these sessions after talking with the organizer.
  Code: sessions = Session.query(Session.typeOfSession == "UNKNOWN")

  1. Regarding the query related problem with implementing a query for non-workshop sessions before 7pm, I found out this would require wanting the session's type to not be workshops and to not be later than 7pm which means two inequality conditions. However, when I first implemented this, I saw that this throws an error "BadRequestException" as shown:
  Problem Code: sessions=Session.query(ndb.AND(Session.typeOfSession!= "WORKSHOP", Session.startTime < sevenTime))
  One solution I came up with is to check for the session's type to be the other possible session types: lecture, keynote, and unknown and the session's start time to be a time before 7pm. The solution I implemented is:
  Solution Code: sessions = Session.query(ndb.AND(Session.startTime < sevenTime,
        ndb.OR(
            Session.typeOfSession == "UNKNOWN",
            Session.typeOfSession == "KEYNOTE",
            Session.typeOfSession == "LECTURE")))

- [Task 4: Add a Task]

  We were tasked with using the Task Queue API to add new session creations to a taskqueue which will update memcache to store the new session if the new session's speaker already has previous sessions. Then by retrieving this speaker and its sessions to assign this speaker as the "featured speaker."

  Explanation is as follows:

  When a new session for a conference is being created, I add this operation to the taskqueue by modeling this after the way a conference is added inside of "createConferenceObject." So inside of "createSessionObject" I add the task and it gets handled in "SetFeaturedSpeakerSessionHandler" inside of main.py. The handler calls "cacheFeaturedSpeakerSessions()", which is modeled after "cacheAnnouncement()". This will validate whether or not to store a Memcache entry for the session that is being added based on whether or not more than one session by the speaker already exists across all conference sessions. The endpoints method "getFeaturedSpeaker" reads the Memecache entries as StringProperty types. If there is not currently anything stored in Memcache, the featured speaker will be displayed as "TBD". However, once a new session is being added in which the session's speaker already has sessions then this session's speaker will become the featured speaker. The featured speaker's name and list of sessions will be displayed.

## Resources used
[1]: Udacity Discussion Forums
[2]: Stack Overflow
[3]: Google App Engine for Python documentation
[4]: https://github.com/udacity/ud858/tree/master/ConferenceCentral_Complete
[5]: Developing Scalable Apps in Python Udacity Course
