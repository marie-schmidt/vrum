"""What the sample project says about its tasks and about what must be true for them to be done
(see tests/demo_data.py, which looks each title up here).

`TASK_DETAILS` holds the details of about two thirds of the tasks, a sentence or three each. A task
that is not here has no details on purpose, so that the board's empty field shows. `ACCEPTS` holds
the acceptance criteria placed on a story or on a task, by its title: a criterion is made open, or
met when the story or task it is on is done. The text is plain and has no numbers or codes in it.
`SIZES` holds how big a task is, and a story that has no tasks (a story with tasks adds up its
tasks), by title: about seven tasks in eight have one, a few are xl and most are s or m.
tests/test_demo.py checks that every title here names a task or a story, that the shares are
right, and that nothing here uses a dash that is not a hyphen."""

TASK_DETAILS: dict[str, str] = {
    # Platform foundations
    "Sign-up form with email and password": (
        "A single screen asks for an email address and a password and nothing else. The password "
        "is checked as it is typed, and a plain message says what is missing."
    ),
    "Email verification link": (
        "After sign-up the rider gets an email with a link that confirms the address. Until it "
        "is clicked the rider can look at the map but cannot unlock a bike."
    ),
    "One-time sign-in code by email": (
        "A rider who does not want a password asks for a code and types it into the app. The "
        "code works once."
    ),
    "Password reset by email": (
        "A rider who forgot the password asks for a link by email. The old password keeps "
        "working until a new one is chosen."
    ),
    "Lock the account for 15 minutes after 10 failed sign-ins": (
        "Too many wrong passwords in a row lock the account for a short while. The rider is told "
        "how long to wait, and an email goes to the address on the account so that the real "
        "owner knows someone is trying."
    ),
    "Sign in with Apple and Google": (
        "Riders can use the account they already have on their phone instead of making a "
        "password. Both buttons lead to the same rider account when the email address matches."
    ),
    "Delete my account and everything attached to it": (
        "A button in the account settings starts the deletion after the rider confirms with the "
        "password. The rider is told what is removed at once and what we must keep for "
        "accounting, and for how long."
    ),
    "Write the data map: every field we store, where and why": (
        "A table of every piece of rider data, with where it is stored, why we need it and who "
        "can see it. The data protection officer reads this first, so it has to be complete and "
        "in plain words."
    ),
    "Trim ride routes to start and end 30 days after a ride": (
        "A nightly job reduces old routes to the start station and the end station. It must "
        "never touch a ride that is still open or one with an open dispute."
    ),
    "Export my data as a file": (
        "The rider asks for a file of everything we hold about them and gets a link by email. "
        "The file can be read by a person, not only by a program."
    ),
    "Data protection impact assessment": (
        "A written assessment of the risks of keeping rider locations, and what we do about "
        "each one. It waits for the city's own questions, which have to be answered in the same "
        "document."
    ),
    "Write the API style guide": (
        "A short guide with names, error shapes and examples that every new endpoint follows."
    ),
    "Errors with a stable code and a plain message": (
        "Every error has a code that never changes and a sentence a person can read. The apps "
        "act on the code and show the sentence when they do not know the code."
    ),
    "Rate limits per client": (
        "Dropped: the gateway already limits requests per client, so our own limits would only "
        "disagree with it. The work moved to the gateway's settings."
    ),
    "Write a log line for every staff change": (
        "Every change a staff member makes to a bike, a station or a rider's account writes one "
        "line. The line says who, what and when, and holds the value before and after."
    ),
    "Log screen in the operator dashboard": (
        "A page in the dashboard where an administrator can search the log by person, by bike "
        "or by day. It only reads: nothing on the page can change a line."
    ),
    "Staging environment with fake bikes": (
        "A copy of the whole service where the bikes are simulated and nobody is charged. Every "
        "change is tried there before it reaches riders."
    ),
    "Deploy to production without downtime": (
        "New versions are started next to the old ones and take over when they are healthy. A "
        "rider in the middle of a ride should not notice a release."
    ),
    "Check database changes before they run": (
        "Every change to the database is tried on a copy of the real data first. A change that "
        "would lock a busy table for long is refused with a reason."
    ),
    "One-click rollback": (
        "Going back to the previous version takes one action and no meeting. The database "
        "changes of a release have to be safe to leave in place after a rollback."
    ),
    "Dashboard for ride starts and failures": (
        "One page that shows how many unlocks were tried, how many worked and why the others "
        "failed. It is what the engineer on call opens first."
    ),
    "Page the on-call engineer when unlocks fail": (
        "When the share of failed unlocks stays above a limit for a few minutes, the engineer on "
        "call is paged. The page says which stations are worst, so that the first look is in the "
        "right place."
    ),
    "Nightly backup of the database": (
        "A full copy of the database is made every night and kept in a different place from the "
        "database. The copy is encrypted, and only a few people can read it."
    ),
    "Monthly restore drill with a written report": (
        "Once a month someone restores last night's copy into a scratch database and checks "
        "that the rides are there. The report says how long it took and what went wrong, even "
        "when nothing did."
    ),
    "Colour and type tokens for light and dark": (
        "One list of colours and type sizes that both apps read, with a light and a dark "
        "version of each. Every pair of text and background was checked for contrast."
    ),
    "Icon set for bikes, stations and warnings": (
        "A small set of icons drawn once and used in both apps. Each icon has a plain name so "
        "that a screen reader can say what it is."
    ),
    # Find and unlock a bike
    "Print QR codes on every bike": (
        "Each bike gets a code on the frame that holds its number and nothing else. The code is "
        "printed on a material that survives rain, sun and a scrubbing brush."
    ),
    "Scan screen with a torch button": (
        "The camera opens straight on the scan screen and finds the code by itself. A button "
        "turns on the torch for dark streets and underground stations."
    ),
    "Signed one-time unlock token": (
        "The server makes a token that proves one rider may open one bike once. It is signed so "
        "that it cannot be forged, and it expires quickly."
    ),
    "Unlock request endpoint": (
        "The endpoint where the app asks to open a bike with the token it was given. It checks "
        "the rider, the bike and the price rules before it tells the lock to open."
    ),
    "Show a clear reason when the unlock fails": (
        "Every failure gets a sentence that says what happened and what the rider can do next. "
        "A rider should never see only that something went wrong."
    ),
    "Retry once on a weak connection": (
        "If the request times out, the app tries one more time before it gives up. The second "
        "try uses the same token, so the rider is never charged twice."
    ),
    "Try the unlock on five older phones": (
        "A short test on the oldest phones we still support, done by a person with a real bike. "
        "The result is written down with the phone model and what happened."
    ),
    "Check the number against bikes nearby": (
        "A typed number only works if that bike is really close to the phone. This stops "
        "someone from opening a bike on the other side of town."
    ),
    "Reserve endpoint with a ten minute timer": (
        "The server keeps the bike for the rider and starts a timer. When the timer ends the "
        "bike is free again without anyone asking."
    ),
    "Countdown on the map and a reminder at two minutes": (
        "The reserved bike shows a clock that counts down. A short reminder appears when two "
        "minutes are left, in case the rider is still walking."
    ),
    "Release the bike when the timer ends": (
        "When the timer ends the bike goes back to the pool and shows on the map again. The "
        "rider is told that the reservation is over."
    ),
    "Allow only one reservation per rider": (
        "A rider who already holds a reservation cannot make a second one. The message shows "
        "how much time is left on the first."
    ),
    "Invite a friend by phone number": (
        "The rider types the number of the friend and the friend gets a message with an unlock "
        "link. The friend does not need an account to open the second bike."
    ),
    "Show both bikes on the live ride screen": (
        "While two bikes are out the ride screen shows both of them, each with its own clock. "
        "Ending one bike does not end the other."
    ),
    "Detect a lock that did not open": (
        "The lock reports back when it moves, and the server compares that with what was asked. "
        "If nothing arrives within a few seconds the unlock is marked as failed."
    ),
    "Offer the nearest other bike": (
        "After a failed unlock the app shows the closest bike that is free, with the walking "
        "distance. One tap reserves it."
    ),
    "Report the broken lock to operations": (
        "A failed unlock sends the bike number and the time to the operations team. It waits "
        "for the lock vendor's answer on how to read a lock that is stuck."
    ),
    "Prototype Bluetooth unlock with the lock vendor's kit": (
        "A small test app that opens a real lock over Bluetooth with no mobile signal. The aim "
        "is to learn what works, not to build the final feature."
    ),
    "Decide how the lock proves the rider may open it": (
        "Without the server the lock has to trust something the phone brings along. The choice "
        "and the reasons are written down as a note, because the safety of every bike depends "
        "on it."
    ),
    # Ride tracking and ending a ride
    "Show the price so far": (
        "The ride screen shows what the ride costs at this moment, updated every few seconds. "
        "The free minutes are taken off before the price is shown."
    ),
    "Detect that the bike is docked": (
        "The dock tells the server when a bike is locked in. A bike that only leans on a dock "
        "without being locked does not count."
    ),
    "Confirm the end of the ride on the phone": (
        "When the lock closes the phone shows the final time and price and a button to confirm. "
        "The rider can leave without pressing it, and the ride still ends."
    ),
    "Handle a bike returned to a full station": (
        "When every dock is taken the app shows the nearest station with a free dock. The rider "
        "is not charged for the extra minutes spent looking."
    ),
    "Allow ending a ride outside a station for a fee": (
        "A rider who cannot find a free dock may park in a marked area and pay a flat fee. The "
        "fee is shown before the rider confirms."
    ),
    "Take a photo of the parked bike": (
        "A rider who ends the ride outside a station takes a photo of where the bike is. The "
        "photo helps the van drivers find it and settles disputes."
    ),
    "Drop points with poor accuracy": (
        "A location point with a wide error is thrown away before it is stored. The route "
        "should follow the street, not jump across a river."
    ),
    "Simplify the route when the ride ends": (
        "The many points of a ride are reduced to a smooth line with far fewer points. The line "
        "still has to look like the path the rider took."
    ),
    "List of past rides": (
        "A simple list with the date, the stations and the price of each ride, newest first. "
        "Tapping a ride opens its details."
    ),
    "Download all my rides": (
        "One button that makes a file of every ride with its price, time and route. The file "
        "arrives by email as a download link."
    ),
    "Queue ride events on the phone": (
        "While there is no signal the phone keeps every ride event on itself. Nothing is lost "
        "if the app is closed or the phone restarts."
    ),
    # Payments, passes and receipts
    "Choose and sign up with the payment provider": (
        "The team compared three providers on price, on how they handle cards in our country and "
        "on how good their support is. The choice and the reasons are written down in a note."
    ),
    "Hosted card entry page": (
        "The rider types the card number on a page that belongs to the payment provider, not to "
        "us. Our servers only ever see a token that stands for the card."
    ),
    "Charge the rider when the ride ends": (
        "When a ride ends the server asks the provider to take the price from the rider's card. "
        "It must never charge twice for the same ride, even when the request is repeated."
    ),
    "Retry a failed charge for three days": (
        "A charge that fails is tried again quietly, a few times a day. The rider is asked to "
        "update the card only when all the tries have failed."
    ),
    "3-D Secure challenge in the app": (
        "Some banks ask the rider to confirm a payment in their own app or with a code. It is "
        "blocked until the provider tells us how the challenge can open inside our app."
    ),
    "Pass plans and prices": (
        "A short table of the passes we sell, what each one covers and what it costs per month. "
        "It is the one place where the prices are written down, and the app reads it."
    ),
    "Buy a pass in the app": (
        "A rider chooses a plan, confirms the card and has the pass at once. The first ride "
        "after buying already uses it."
    ),
    "Show the free minutes left this month": (
        "A rider with a pass sees how many free minutes are left before the next charge. The "
        "number is on the account screen and on the unlock screen."
    ),
    "Refund a single ride from the operator dashboard": (
        "A support person finds the ride, chooses an amount and writes the reason. The refund is "
        "recorded against the ride so that it cannot be given twice."
    ),
    "Refunds go back to the original card": (
        "The money is sent to the card that paid and to no other. The rider gets an email that "
        "says how long the bank may take."
    ),
    "Send the receipt email when the ride ends": (
        "As soon as the charge is settled the rider gets one email with the price, the route "
        "and the time. The email is made from the same numbers that were charged."
    ),
    "Price table by ride length": (
        "A table that turns minutes into money, with the first minutes free and a lower price "
        "per minute for long rides. The table is data, so changing a price does not need a "
        "release."
    ),
    "Five free minutes on every single ride": (
        "The first minutes of a single ride cost nothing. They are taken off before the daily "
        "cap is worked out."
    ),
    "Daily cap on what a rider pays": (
        "No rider pays more than the cap in one day, however many rides they take. The cap is "
        "shown in the app so that nobody is surprised."
    ),
    # Stations and live availability
    "Map screen with clustering": (
        "Stations that are close together on the screen are shown as one circle with a number. "
        "Zooming in splits the circle into the real stations."
    ),
    "Station marker with bikes and free docks": (
        "Each marker shows the number of bikes and the number of free docks, in colours that "
        "also work for riders who cannot tell red from green. A marker with no recent count is "
        "grey."
    ),
    "Refresh the counts every 30 seconds": (
        "The app asks for fresh bike and dock counts twice a minute while the map is open. A "
        "station that has not reported for a minute is shown as unknown."
    ),
    "Keep the map smooth with 200 stations": (
        "The map should pan and zoom without stutter on the oldest phone we support. Only the "
        "stations on the screen are drawn."
    ),
    "Inventory every setting the old service reads": (
        "A list of every setting the old service reads, with its value in each environment. "
        "Nothing may be left to the memory of whoever wrote the service."
    ),
    "Move the settings to the new configuration service": (
        "Each setting moves on its own, with the old service reading the new place. After every "
        "move the stations are watched for a day."
    ),
    "Search box with suggestions": (
        "The rider types and the app suggests stations and streets while typing. The "
        "suggestions come from the server, so they are the same in both apps."
    ),
    "Nearest free dock when a station is full": (
        "When the station a rider is heading to is full the app points to the closest one with "
        "free docks. It shows the walking distance and updates as the counts change."
    ),
    "Nearest bike when a station is empty": (
        "When a station has no bikes the app points to the closest one that has. It says how "
        "many bikes are there, so the rider does not walk to an empty station twice."
    ),
    # Fleet operations and maintenance
    "Checklist screen": (
        "The technician goes through the same list for every bike, one question at a time. A "
        "bike cannot be signed off while a question is open."
    ),
    "Photo as proof": (
        "Every inspection ends with a photo of the bike, taken in the app. The photo is stored "
        "with the inspection so that a disagreement can be settled later."
    ),
    "Sign-off with the technician's name": (
        "At the end of an inspection the signed-in technician confirms the result. The name on "
        "the inspection always comes from the sign-in."
    ),
    "Rider can report damage in two taps": (
        "A report is one tap on the bike and one tap on what is wrong. A photo is welcome but "
        "never required."
    ),
    "Lock out a bike after two reports": (
        "The second report from a different rider locks the bike at once. It stays locked until "
        "a technician has checked it."
    ),
    "Damage report endpoint for operators": (
        "The operations team can read every report with its photos and the bike's history. It "
        "is the one place an operator tool reads damage from."
    ),
    "Tell the nearest technician": (
        "A locked-out bike sends a message to the technician who is closest and not busy. The "
        "message holds the bike number, the fault and where the bike is."
    ),
    "Tag a locked-out bike with its fault": (
        "The technician writes down what is wrong with the bike. The tag is shown on the bike's "
        "page and in the workshop list."
    ),
    "Plan the swaps for the day": (
        "Every morning the app makes a list of the bikes whose battery will not last the day. "
        "The list is ordered so that the van drives the shortest way."
    ),
    "Alert at 20 percent charge": (
        "A bike whose battery drops below the alert level is added to the next swap list. The "
        "rider on the bike is told if the charge will not reach the station."
    ),
    "Job card for each repair": (
        "Every repair has a card that holds the fault, the work done and who did it. The card "
        "is written against the bike's own number."
    ),
    "Repair history on the bike's page": (
        "The bike's page lists every repair, newest first. A technician can see at a glance "
        "what was done before."
    ),
    "Workshop view on a tablet": (
        "A layout for the tablet in the workshop with large buttons and no typing where a tap "
        "will do. It works with gloves on and with oily hands."
    ),
    "Wear model from the distance ridden": (
        "Dropped: a model that guesses when a tyre is worn out from the distance ridden. The "
        "inspections already catch worn tyres, so the guess adds little."
    ),
    "Check that every bike has a readable number": (
        "Technicians check the number on every bike when they do an inspection. A bike with a "
        "faded number is listed for repainting."
    ),
    # Safety, incidents and insurance
    "Crash report form in the app": (
        "A short form with big buttons, made for someone who is shaken. It asks what happened, "
        "where, and whether anyone is hurt."
    ),
    "Send the city a report within 24 hours": (
        "Every injury report is sent to the city in the form the city asked for, within a day. "
        "Weekends and holidays count, so someone on call has to be able to send it."
    ),
    "Show first aid and emergency numbers": (
        "The first screen of the crash form shows the emergency number and a button to call it. "
        "First aid advice is one tap further."
    ),
    "Call the rider the next day": (
        "A person from operations calls the rider the day after a crash report to ask how they "
        "are. The call and what was said are written on the report."
    ),
    "Show a helmet reminder before the first ride": (
        "Before the very first ride the app shows a friendly reminder to wear a helmet. It can "
        "be dismissed with one tap and is never shown again."
    ),
    "Insurance terms text from the insurer": (
        "The insurer sends the terms as text, and we show them exactly as written. Any change "
        "to the text counts as a new version that riders must accept again."
    ),
    "Rider accepts the terms before the first ride": (
        "The unlock is blocked until the rider has accepted the terms. The acceptance is stored "
        "with the date and the version of the text."
    ),
    # Rider support
    "Write the first twenty articles": (
        "Articles written from the questions riders really asked in the pilot, most common "
        "first. Each answer is short enough to read while standing in the street."
    ),
    "Show help from the failed unlock screen": (
        "The screen that says an unlock failed has a button that opens the article for that "
        "problem. The rider does not have to search for it."
    ),
    "Tags for unlock, charge, bike and other": (
        "Every message in the inbox gets one of four tags, so that the weekly report can count "
        "the common causes. A message cannot be closed without a tag."
    ),
    "Reply templates": (
        "A few ready answers for the questions that come every day. The person answering can "
        "change the text before sending."
    ),
    # City partnership and reporting
    "Count rides by station and hour": (
        "A table of how many rides started and ended at each station in each hour of the month. "
        "The totals must match the number of finished rides."
    ),
    "Report layout that the city asked for": (
        "The city sent a template with its own station names and its own order of columns. Our "
        "report follows it exactly, so the city can paste it into its own papers."
    ),
    "Define what an anonymous trip record holds": (
        "A short list of the fields that may be in a public trip record, and a list of fields "
        "that never may. The data protection officer has to agree to the lists before anything "
        "is published."
    ),
    "Leave out trips shorter than 60 seconds": (
        "A trip of less than a minute is usually a mistake or a failed unlock. These trips are "
        "left out so that the public data is not misleading."
    ),
    "Permits for 20 more stations": (
        "The city must give a permit for each new station. We send the paperwork in groups and "
        "keep track of which stations are still waiting."
    ),
    # Notifications and messages
    "Push when a reservation is about to end": (
        "Shortly before a reservation ends the rider gets a short push. It says how long is "
        "left and which bike it is."
    ),
    "Push when a ride has been open for two hours": (
        "A ride that has been open for a long time is often a forgotten one. The push asks the "
        "rider whether they are still riding."
    ),
    "Do not push between 22:00 and 07:00": (
        "During the quiet hours no push is sent unless it is about safety. A message that was "
        "held back is dropped if it no longer matters."
    ),
    "Write and preview the message": (
        "The operations person writes the message and sees exactly how it will look on a phone. "
        "Nothing is sent until they press the button."
    ),
    "Send in the language of each rider": (
        "Each rider gets the message in the language chosen in their app. A message with no "
        "translation is sent in the default language and says so."
    ),
}

ACCEPTS: dict[str, tuple[str, ...]] = {
    # Platform foundations
    "Sign-up form with email and password": (
        "The form asks for an email address and a password and nothing else",
        "A password that is too short is refused with a plain message",
    ),
    "Email verification link": ("The link works once and then stops working",),
    "Password reset by email": (
        "The old password keeps working until a new one is chosen",
        "A reset link stops working after a short time",
    ),
    "Lock the account for 15 minutes after 10 failed sign-ins": (
        "The rider is told how long to wait",
        "A correct password during the lock does not open the account",
    ),
    "Sign in with Apple and Google": (
        "Both buttons lead to the same rider account when the email address matches",
    ),
    "Delete my account and everything attached to it": (
        "The rider confirms with the password before anything is removed",
    ),
    "Write the data map: every field we store, where and why": (
        "Every field the apps and servers store is in the map",
        "Each field says why we need it",
    ),
    "Trim ride routes to start and end 30 days after a ride": (
        "A ride that is still open is never trimmed",
        "A ride with an open dispute is never trimmed",
    ),
    "Export my data as a file": ("The file can be read by a person, not only by a program",),
    "Data protection impact assessment": ("Every risk in the assessment says what we do about it",),
    "API conventions and versioning": ("Every endpoint follows the style guide",),
    "Errors with a stable code and a plain message": (
        "A code never changes once it is released",
        "Every error has a sentence a person can read",
    ),
    "Version prefix on every route": ("No route can be reached without its version",),
    "Audit log of staff actions": ("Every staff change leaves a line that nobody can edit",),
    "Write a log line for every staff change": (
        "A line says who made the change, what it was before and after, and when",
    ),
    "Keep the log for seven years": ("A line is never deleted before seven years have passed",),
    "Environments and release pipeline": (
        "A change reaches riders only after it was built and tested",
    ),
    "Deploy to production without downtime": (
        "A rider in the middle of a ride notices nothing during a release",
    ),
    "One-click rollback": ("Going back takes one action and only a few minutes",),
    "Monitoring and on-call alerts": (
        "We see failing unlocks before riders write to us about them",
    ),
    "Page the on-call engineer when unlocks fail": (
        "The engineer is paged within minutes of the first failures",
        "The page says which stations and which bikes are affected",
    ),
    "Backups and a restore drill": ("A backup has been restored successfully in the last month",),
    "Nightly backup of the database": ("A backup exists for every night of the last month",),
    "Monthly restore drill with a written report": ("The report says how long the restore took",),
    "Design system and shared components": (
        "Colours, type and buttons look the same in both apps",
    ),
    "Colour and type tokens for light and dark": (
        "Every pair of text and background is readable in both themes",
    ),
    # Find and unlock a bike
    "Print QR codes on every bike": (
        "The code can be scanned in rain and at night",
        "A scratched code still scans",
    ),
    "Signed one-time unlock token": (
        "A token works once",
        "A token is useless after it expires",
    ),
    "Lock opens within two seconds": ("The lock opens within two seconds on a good connection",),
    "Show a clear reason when the unlock fails": ("The reason says what the rider can do next",),
    "Try the unlock on five older phones": (
        "Every phone on the list unlocks a bike at least nine times out of ten",
    ),
    "Check the number against bikes nearby": (
        "A number that is not nearby is refused with a clear reason",
    ),
    "Reserve endpoint with a ten minute timer": ("The clock on the server is the one that counts",),
    "Allow only one reservation per rider": (
        "A second reservation is refused and shows the time left on the first",
    ),
    "Unlock a second bike for a friend": (
        "The account that pays is the account that owns both rides",
    ),
    "Invite a friend by phone number": ("A friend who has no account is told how to get one",),
    "Recover from a failed unlock": ("A rider whose lock did not open always gets a next step",),
    "Detect a lock that did not open": (
        "The app knows within seconds that the lock stayed closed",
    ),
    "Offer the nearest other bike": ("The bike offered is the nearest one that is free",),
    "Report the broken lock to operations": ("Operations see the report with the bike number",),
    (
        "Let a rider who loses mobile signal at the station still unlock a bike over Bluetooth, "
        "and keep the ride timer honest until the phone is online again"
    ): ("A rider with no signal can still open a lock at a station that has none",),
    "Prototype Bluetooth unlock with the lock vendor's kit": (
        "The prototype opens a real lock with no mobile signal",
    ),
    "Decide how the lock proves the rider may open it": (
        "The decision is written down as a note with its reasons",
    ),
    # Ride tracking and ending a ride
    "Ride timer and live ride screen": (
        "The clock on the screen matches the clock used for the price",
    ),
    "Show the price so far": ("The price on the screen never differs from the final price",),
    "Detect that the bike is docked": (
        "A bike that is only leaning on a dock does not end the ride",
    ),
    "Handle a bike returned to a full station": (
        "The rider is shown the nearest station with a free dock",
    ),
    "Allow ending a ride outside a station for a fee": (
        "The fee is shown before the rider confirms",
    ),
    "Drop points with poor accuracy": ("A point that is far off the street is dropped",),
    "Simplify the route when the ride ends": (
        "The simplified route still looks like the street the rider took",
    ),
    "Ride history and receipts": ("A rider can find any ride of the last year",),
    "List of past rides": ("The newest ride is at the top",),
    "Queue ride events on the phone": ("No ride event is lost when the phone restarts",),
    # Payments, passes and receipts
    "Hosted card entry page": ("The card number is typed only on the provider's page",),
    "Charge the rider when the ride ends": ("A rider is never charged twice for one ride",),
    "Retry a failed charge for three days": ("Retries stop as soon as one works",),
    "3-D Secure challenge in the app": ("The challenge can be completed without leaving the app",),
    "Pass plans and prices": ("Both plans show what they cost per month",),
    "Buy a pass in the app": ("A rider who already has a pass cannot buy a second one",),
    "Refund a single ride from the operator dashboard": ("A ride can be refunded only once",),
    "Send the receipt email when the ride ends": (
        "The email arrives within minutes of the end of the ride",
    ),
    "Prices, free minutes and daily caps": (
        "The price on the unlock screen is the price that is charged",
    ),
    "Price table by ride length": ("The table has no gaps between ride lengths",),
    "Five free minutes on every single ride": (
        "The free minutes are taken off before the daily cap is applied",
    ),
    "Daily cap on what a rider pays": ("A rider is never charged more than the cap in one day",),
    # Stations and live availability
    "Map screen with clustering": ("A cluster opens into single stations when the rider zooms in",),
    "Refresh the counts every 30 seconds": (
        "A station that did not report for a minute is shown as unknown",
    ),
    "Keep the map smooth with 200 stations": (
        "Panning stays smooth on the oldest phone we support",
    ),
    "Migrate LegacyStationTelemetryIngestionServiceConfiguration settings": (
        "No setting is lost in the move",
    ),
    "Inventory every setting the old service reads": (
        "Every setting has a place in the new service",
    ),
    "Search for a station or an address": (
        "A rider finds a station by typing part of its street name",
    ),
    "Search box with suggestions": ("Suggestions appear within a second of typing",),
    "Tell riders where the next free dock or bike is": (
        "The station that is suggested is never one that is closed",
    ),
    "Nearest free dock when a station is full": ("The distance shown is the walking distance",),
    # Fleet operations and maintenance
    "Checklist screen": ("An inspection cannot be saved with a question left open",),
    "Sign-off with the technician's name": (
        "The name comes from the signed-in technician and is never typed",
    ),
    "Rider can report damage in two taps": (
        "A report can be sent with one hand while standing next to the bike",
    ),
    "Lock out a bike after two reports": ("A locked-out bike cannot be unlocked by a rider",),
    "Damage report endpoint for operators": ("Operators see the photos that came with the report",),
    "Battery swap schedule": ("No electric bike runs flat in the middle of a ride",),
    "Plan the swaps for the day": ("Every bike under the alert level is on the plan",),
    "Workshop jobs and repair history for each bike": (
        "Every repair is written against the bike's own number",
    ),
    "Job card for each repair": ("A job card cannot be closed without a bike number",),
    "Repair history on the bike's page": ("The history shows the newest repair first",),
    "Check that every bike has a readable number": (
        "A bike with a faded number is listed for repainting",
    ),
    # Safety, incidents and insurance
    "Crash report form in the app": ("A shaken person can fill in the form in about a minute",),
    "Send the city a report within 24 hours": ("Weekends and holidays are included",),
    "Show first aid and emergency numbers": ("The emergency number can be called with one tap",),
    "Helmet reminders and partner discount": (
        "A rider is shown the helmet reminder once and never again",
    ),
    "Show a helmet reminder before the first ride": ("The reminder can be dismissed with one tap",),
    "Rider accepts the terms before the first ride": (
        "The acceptance is stored with the date and the version of the terms",
    ),
    # Rider support
    "Write the first twenty articles": ("Every article answers a question a rider really asked",),
    "Show help from the failed unlock screen": (
        "The article opens on the problem the rider just had",
    ),
    "Support inbox for the team": ("Every message in the inbox has an owner",),
    "Shared inbox for support": ("No message is shown to two people as their own",),
    "Tags for unlock, charge, bike and other": ("A message cannot be closed without a tag",),
    # City partnership and reporting
    "Monthly ridership report for the city": (
        "The city can open the report without asking us for help",
    ),
    "Count rides by station and hour": ("The total matches the number of finished rides",),
    "Report layout that the city asked for": ("The layout is the one in the city's own template",),
    "Open data feed of anonymous trips": ("No trip in the public file can be traced to a rider",),
    "Define what an anonymous trip record holds": (
        "The record holds no rider, no bike and no exact time",
    ),
    "Station signs and permits": ("Every station that is open has a permit and a sign",),
    "Permits for 20 more stations": ("A station is opened only after its permit has arrived",),
    # Notifications and messages
    "Push notifications for reservations and rides": (
        "A rider is never nudged about something that already happened",
    ),
    "Push when a reservation is about to end": (
        "The push arrives a couple of minutes before the reservation ends",
    ),
    "Push when a ride has been open for two hours": (
        "A rider who ended the ride never gets this push",
    ),
    "Quiet hours": ("A message about safety is never held back",),
    "Do not push between 22:00 and 07:00": ("The hours follow the rider's own time zone",),
    "Message every rider at one station": (
        "A message reaches only the riders who used the station",
    ),
    "Write and preview the message": ("The preview shows exactly what the rider will read",),
}

# How big a task, or a story that has no tasks, is: effort and context together (xs, s, m, l or xl).
# A story with tasks has no size of its own: it adds up its tasks. About an eighth of the tasks
# are left without a size on purpose, so that the board shows work that still needs one.
SIZES: dict[str, str] = {
    # Platform foundations
    "Sign-up form with email and password": "m",
    "Email verification link": "s",
    "One-time sign-in code by email": "m",
    "Password reset by email": "m",
    "Lock the account for 15 minutes after 10 failed sign-ins": "s",
    "Sign in with Apple and Google": "l",
    "Delete my account and everything attached to it": "l",
    "Write the data map: every field we store, where and why": "m",
    "Trim ride routes to start and end 30 days after a ride": "s",
    "Export my data as a file": "m",
    "Data protection impact assessment": "l",
    "Write the API style guide": "m",
    "Version prefix on every route": "xs",
    "Errors with a stable code and a plain message": "s",
    "Pagination with opaque cursors": "m",
    "Rate limits per client": "m",
    "Use the gateway's rate limits": "s",
    "Write a log line for every staff change": "m",
    "Log screen in the operator dashboard": "m",
    "Build and test on every push": "m",
    "Staging environment with fake bikes": "m",
    "Deploy to production without downtime": "l",
    "Check database changes before they run": "m",
    "One-click rollback": "m",
    "Dashboard for ride starts and failures": "m",
    "Page the on-call engineer when unlocks fail": "s",
    "Weekly on-call rota": "xs",
    "Nightly backup of the database": "s",
    "Monthly restore drill with a written report": "m",
    "Colour and type tokens for light and dark": "s",
    "Button, input and list components": "m",
    "Icon set for bikes, stations and warnings": "m",
    "Feature flags": "m",
    # Find and unlock a bike
    "Print QR codes on every bike": "s",
    "Camera permission screen": "xs",
    "Scan screen with a torch button": "s",
    "Signed one-time unlock token": "m",
    "Unlock request endpoint": "m",
    "Lock opens within two seconds": "m",
    "Show a clear reason when the unlock fails": "s",
    "Retry once on a weak connection": "s",
    "Bike number fallback when the camera is dark": "s",
    "Vibrate and play a sound when the lock opens": "xs",
    "Try the unlock on five older phones": "m",
    "Number pad screen": "s",
    "Check the number against bikes nearby": "m",
    "Reserve endpoint with a ten minute timer": "m",
    "Countdown on the map and a reminder at two minutes": "m",
    "Release the bike when the timer ends": "s",
    "Allow only one reservation per rider": "s",
    "Show reserved bikes as unavailable to everyone else": "s",
    "Detect a lock that did not open": "m",
    "Offer the nearest other bike": "m",
    "Report the broken lock to operations": "s",
    "Refund the unlock fee automatically": "m",
    "Prototype Bluetooth unlock with the lock vendor's kit": "xl",
    "Decide how the lock proves the rider may open it": "m",
    "Keep the ride timer honest while offline": "l",
    # Ride tracking and ending a ride
    "Count up the ride time": "xs",
    "Show the price so far": "s",
    "Keep the screen awake during a ride": "xs",
    "Detect that the bike is docked": "m",
    "Confirm the end of the ride on the phone": "s",
    "Handle a bike returned to a full station": "m",
    "Allow ending a ride outside a station for a fee": "m",
    "Lock the bike remotely after the rider walks away": "m",
    "Location point every ten seconds": "s",
    "Drop points with poor accuracy": "s",
    "Simplify the route when the ride ends": "m",
    "Show the route on the receipt": "s",
    "List of past rides": "s",
    "Ride details with the route": "m",
    "Download all my rides": "s",
    # Payments, passes and receipts
    "Choose and sign up with the payment provider": "m",
    "Hosted card entry page": "m",
    "Charge the rider when the ride ends": "l",
    "Retry a failed charge for three days": "m",
    "3-D Secure challenge in the app": "m",
    "Show the price before the ride starts": "s",
    "Say clearly when a card is expired or stolen": "s",
    "Pass plans and prices": "m",
    "Buy a pass in the app": "m",
    "Renew a pass on the same day each month": "m",
    "Show the free minutes left this month": "s",
    "Pause a pass for up to 30 days": "m",
    "Refund a single ride from the operator dashboard": "m",
    "Refunds go back to the original card": "m",
    "Send the receipt email when the ride ends": "s",
    "Receipt layout in the three launch languages": "m",
    "Resend a receipt from the operator dashboard": "xs",
    "Price table by ride length": "m",
    "Five free minutes on every single ride": "s",
    "Daily cap on what a rider pays": "s",
    "Different prices in different city zones": "l",
    "Student and low income discount": "l",
    # Stations and live availability
    "Map screen with clustering": "m",
    "Station marker with bikes and free docks": "s",
    "Refresh the counts every 30 seconds": "s",
    "Show reserved and broken bikes differently": "xs",
    "Dark map style": "s",
    "Keep the map smooth with 200 stations": "m",
    "Hold a bike for a rider before they arrive": "m",
    "Inventory every setting the old service reads": "m",
    "Move the settings to the new configuration service": "m",
    "Delete the old configuration after one clean week": "xs",
    "Search box with suggestions": "m",
    "Search by street address": "m",
    "Remember recent searches": "xs",
    "Nearest free dock when a station is full": "m",
    "Nearest bike when a station is empty": "m",
    # Fleet operations and maintenance
    "Checklist screen": "s",
    "Photo as proof": "s",
    "Sign-off with the technician's name": "xs",
    "History of inspections for each bike": "s",
    "Rider can report damage in two taps": "s",
    "Lock out a bike after two reports": "m",
    "Damage report endpoint for operators": "m",
    "Tell the nearest technician": "s",
    "Tag a locked-out bike with its fault": "xs",
    "Unlock a bike again after the repair": "s",
    "Plan the swaps for the day": "m",
    "Alert at 20 percent charge": "s",
    "Job card for each repair": "m",
    "Repair history on the bike's page": "s",
    "Time spent on each job": "s",
    "Parts used on each job": "s",
    "Workshop view on a tablet": "m",
    "Wear model from the distance ridden": "m",
    "Alert when a tyre is due": "s",
    "Order paint stencils": "xs",
    "Check that every bike has a readable number": "s",
    # Safety, incidents and insurance
    "Crash report form in the app": "m",
    "Send the city a report within 24 hours": "m",
    "Show first aid and emergency numbers": "s",
    "Attach photos to a report": "s",
    "Insurance terms text from the insurer": "s",
    "Rider accepts the terms before the first ride": "s",
    "Lights check before an evening ride": "s",
    # Rider support
    "Write the first twenty articles": "l",
    "Search inside the help centre": "m",
    "Show help from the failed unlock screen": "s",
    "Mark every article with the day it was last checked": "xs",
    "Shared inbox for support": "m",
    "Tags for unlock, charge, bike and other": "s",
    "Reply templates": "s",
    "Weekly report of missed promises": "m",
    "Chat with support": "xl",
    # City partnership and reporting
    "Count rides by station and hour": "m",
    "Report layout that the city asked for": "m",
    "Send the report on the first of each month": "s",
    "Check the numbers with the city once": "xs",
    "Define what an anonymous trip record holds": "m",
    "Publish a daily file": "m",
    "Leave out trips shorter than 60 seconds": "xs",
    "Sign design agreed with the city": "s",
    "Permits for 20 more stations": "m",
    "Print and mount the station signs": "m",
    # Notifications and messages
    "Push when a reservation is about to end": "s",
    "Push when a ride has been open for two hours": "s",
    "Settings to turn each kind off": "m",
    "Send the rider an email receipt when a ride ends": "s",
    "Pick the station on a map": "m",
    "Write and preview the message": "m",
    "Send in the language of each rider": "m",
    # Rebalancing: moving bikes between stations
    "Night-time moves between stations": "l",
}
