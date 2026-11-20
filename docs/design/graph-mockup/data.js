/* The demo project of Vrum, "Riverside Bike Share" (tests/demo_data.py), as the board numbers and names it.
   REAL: every epic, story and task with its number, title, status, priority and due date (days from the demo's
   day, 2026-11-18), every link, requirement, decision and fact with its id, title, text, who decided it, when,
   what it concerns and whether it is confirmed or replaced or withdrawn.
   SHORTENED: the titles of the nine long project-wide decisions.
   ILLUSTRATIVE: the dates of facts and of confirmations (the demo records them all on one day), the date of
   "your last visit", and every "alike" score except the two the real board shows for S-1 (the demo's scripted
   index gives other numbers than a real embedding model would). */
(function () {
  'use strict';

  const TODAY = '2026-11-18';
  const dayMs = 86400000;
  const iso = (offset) => new Date(Date.parse(TODAY + 'T00:00:00Z') + offset * dayMs).toISOString().slice(0, 10);

  const STATUS = { n: 'new', p: 'in_progress', d: 'done', b: 'blocked', x: 'dropped' };
  const PRIORITY = { l: 'low', n: 'normal', h: 'high', u: 'urgent' };

  /* -- the plan: epics, stories, tasks, numbered per kind in the order they are made ---------------- */

  const nodes = [];
  const count = { E: 0, S: 0, T: 0 };
  let epic = null;
  let story = null;

  function add(kind, letter, title, sp, due, held, parent) {
    count[letter] += 1;
    const node = {
      kind,
      code: letter + '-' + count[letter],
      title,
      status: STATUS[sp[0]],
      priority: PRIORITY[sp[1]],
      due: due === null ? null : iso(due),
      held: held || null,
      parent,
    };
    nodes.push(node);
    return node;
  }
  const E = (title, sp, due) => {
    epic = add('epic', 'E', title, sp, due, null, null);
    story = null;
  };
  const S = (title, sp, due, held) => {
    story = add('story', 'S', title, sp, due, held, epic.code);
  };
  const T = (title, sp, due, held) => {
    add('task', 'T', title, sp, due, held, story.code);
  };

  E("Platform foundations", "ph", null);
    S("Rider accounts and sign-in", "ph", 21);
      T("Sign-up form with email and password", "dh", -80);
      T("Email verification link", "dn", -74);
      T("One-time sign-in code by email", "dn", -66);
      T("Password reset by email", "dn", -60);
      T("Lock the account for 15 minutes after 10 failed sign-ins", "ph", -3, "Lena Okafor");
      T("Sign in with Apple and Google", "nn", 21);
      T("Delete my account and everything attached to it", "nh", 14);
    S("Privacy and data retention", "pu", 28);
      T("Write the data map: every field we store, where and why", "du", -40);
      T("Trim ride routes to start and end 30 days after a ride", "ph", -2);
      T("Export my data as a file", "nn", 30);
      T("Data protection impact assessment", "bu", -9);
      T("Cookie-free analytics for the website", "nl", null);
    S("API conventions and versioning", "dn", -28);
      T("Write the API style guide", "dn", -55);
      T("Version prefix on every route", "dn", -48);
      T("Errors with a stable code and a plain message", "dn", -42);
      T("Pagination with opaque cursors", "dn", -38);
      T("Rate limits per client", "xl", -30);
      T("Use the gateway's rate limits", "dn", -28);
    S("Audit log of staff actions", "nn", 45);
      T("Write a log line for every staff change", "nn", 38);
      T("Log screen in the operator dashboard", "nn", 45);
      T("Keep the log for seven years", "nl", null);
    S("Environments and release pipeline", "ph", 12);
      T("Build and test on every push", "dn", -85);
      T("Staging environment with fake bikes", "dn", -70);
      T("Deploy to production without downtime", "ph", 3, "api-agent");
      T("Check database changes before they run", "nn", 10);
      T("One-click rollback", "nh", 12);
      T("Release notes written from merged changes", "nl", null);
    S("Monitoring and on-call alerts", "nh", 35);
      T("Dashboard for ride starts and failures", "nn", 20);
      T("Page the on-call engineer when unlocks fail", "nh", 24);
      T("Weekly on-call rota", "nn", 26);
      T("Write the first runbook", "nl", 35);
    S("Feature flags", "nn", null);
    S("Backups and a restore drill", "nn", 40);
      T("Nightly backup of the database", "dh", -45);
      T("Monthly restore drill with a written report", "nn", 40);
    S("Design system and shared components", "pn", 18);
      T("Colour and type tokens for light and dark", "dn", -22);
      T("Button, input and list components", "pn", 4);
      T("Icon set for bikes, stations and warnings", "nn", 18);
  E("Find and unlock a bike", "ph", 30);
    S("Scan a QR code to unlock", "ph", 10);
      T("Print QR codes on every bike", "dn", -70);
      T("Camera permission screen", "dn", -66);
      T("Scan screen with a torch button", "dn", -60);
      T("Signed one-time unlock token", "dh", -58);
      T("Unlock request endpoint", "dn", -52);
      T("Lock opens within two seconds", "pu", -1, "mobile-agent");
      T("Show a clear reason when the unlock fails", "nh", 6);
      T("Retry once on a weak connection", "nn", 8);
      T("Bike number fallback when the camera is dark", "nn", 9);
      T("Vibrate and play a sound when the lock opens", "nl", 12);
      T("Try the unlock on five older phones", "nn", 14);
      T("Write the unlock help text", "nl", null);
    S("Unlock by typing the bike number", "dn", -20);
      T("Number pad screen", "dn", -30);
      T("Check the number against bikes nearby", "dn", -25);
    S("Reserve a bike for ten minutes", "nn", 40);
      T("Reserve endpoint with a ten minute timer", "nn", 30);
      T("Countdown on the map and a reminder at two minutes", "nn", 34);
      T("Release the bike when the timer ends", "nn", 36);
      T("Allow only one reservation per rider", "nn", 36);
      T("Show reserved bikes as unavailable to everyone else", "nn", 38);
    S("Unlock a second bike for a friend", "nl", 70);
      T("Invite a friend by phone number", "nl", null);
      T("Pay for two bikes on one account", "nl", null);
      T("Show both bikes on the live ride screen", "nl", null);
    S("Recover from a failed unlock", "bh", -4);
      T("Detect a lock that did not open", "dn", -12);
      T("Offer the nearest other bike", "ph", -3);
      T("Report the broken lock to operations", "bn", -6);
      T("Refund the unlock fee automatically", "nn", 48);
    S("Let a rider who loses mobile signal at the station still unlock a bike over Bluetooth, and keep the ride timer honest until the phone is online again", "nn", 85);
      T("Prototype Bluetooth unlock with the lock vendor's kit", "nn", 60);
      T("Decide how the lock proves the rider may open it", "nn", 66);
      T("Keep the ride timer honest while offline", "nn", 75);
      T("Test in the car park on Mill Street", "nl", 85);
  E("Ride tracking and ending a ride", "pn", null);
    S("Ride timer and live ride screen", "dn", -26);
      T("Count up the ride time", "dn", -30);
      T("Show the price so far", "dn", -28);
      T("Keep the screen awake during a ride", "dl", -26);
    S("End a ride at a station", "pu", -2);
      T("Detect that the bike is docked", "dn", -20);
      T("Confirm the end of the ride on the phone", "dh", -16);
      T("Handle a bike returned to a full station", "ph", -1);
      T("Allow ending a ride outside a station for a fee", "nn", 12);
      T("Lock the bike remotely after the rider walks away", "nn", 14);
      T("Take a photo of the parked bike", "nl", 30);
    S("Record the route of a ride", "pn", 25);
      T("Location point every ten seconds", "dn", -30);
      T("Drop points with poor accuracy", "pn", 4);
      T("Simplify the route when the ride ends", "nn", 16);
      T("Show the route on the receipt", "nl", 40);
    S("Ride history and receipts", "nn", 50);
      T("List of past rides", "nn", 46);
      T("Ride details with the route", "nn", 48);
      T("Download all my rides", "nl", 50);
    S("Tunnels", "nl", 70);
      T("Queue ride events on the phone", "nl", null);
      T("Send queued events when the signal returns", "nl", null);
  E("Payments, passes and receipts", "ph", 55);
    S("Pay for a ride by card", "pu", 8);
      T("Choose and sign up with the payment provider", "dh", -90);
      T("Hosted card entry page", "dh", -62);
      T("Charge the rider when the ride ends", "pu", 2, "api-agent");
      T("Retry a failed charge for three days", "nn", 9);
      T("3-D Secure challenge in the app", "bh", 6);
      T("Show the price before the ride starts", "nh", 7);
      T("Say clearly when a card is expired or stolen", "nn", 10);
    S("Monthly and annual passes", "nh", 40);
      T("Pass plans and prices", "nh", 28);
      T("Buy a pass in the app", "nn", 34);
      T("Renew a pass on the same day each month", "nn", 38);
      T("Show the free minutes left this month", "nn", 40);
      T("Pause a pass for up to 30 days", "nl", 45);
    S("Refunds and credits", "nn", 44);
      T("Refund a single ride from the operator dashboard", "nn", null);
      T("Refunds go back to the original card", "nn", 36);
      T("Credits that expire after one year", "nl", 50);
    S("Email a receipt after every ride", "nn", 30);
      T("Send the receipt email when the ride ends", "nn", 24);
      T("Receipt layout in the three launch languages", "nn", 26);
      T("Resend a receipt from the operator dashboard", "nl", 30);
    S("Student and low income discount", "nn", null);
    S("Prices, free minutes and daily caps", "ph", -6);
      T("Price table by ride length", "ph", -14);
      T("Five free minutes on every single ride", "dn", -20);
      T("Daily cap on what a rider pays", "nn", 3);
      T("Different prices in different city zones", "bn", -5);
  E("Stations and live availability", "pn", 40);
    S("Map of stations with live bike counts", "ph", 6);
      T("Map screen with clustering", "dh", -50);
      T("Station marker with bikes and free docks", "dn", -44);
      T("Refresh the counts every 30 seconds", "pn", 3);
      T("Show reserved and broken bikes differently", "nn", 14);
      T("Dark map style", "nl", 30);
      T("Keep the map smooth with 200 stations", "nn", 20);
    S("Hold a bike for a rider before they arrive", "nl", 60);
    S("Migrate LegacyStationTelemetryIngestionServiceConfiguration settings", "pn", -8);
      T("Inventory every setting the old service reads", "dn", -20);
      T("Move the settings to the new configuration service", "pn", -20);
      T("Delete the old configuration after one clean week", "nn", 10);
    S("Search for a station or an address", "nh", 28);
      T("Search box with suggestions", "nn", 26);
      T("Search by street address", "nn", 28);
      T("Remember recent searches", "nn", 34);
    S("Tell riders where the next free dock or bike is", "nh", 22);
      T("Nearest free dock when a station is full", "nh", 20);
      T("Nearest bike when a station is empty", "nh", 22);
  E("Fleet operations and maintenance", "ph", null);
    S("Inspection checklist on the technician's phone", "dn", -24);
      T("Checklist screen", "dn", -40);
      T("Photo as proof", "dn", -36);
      T("Sign-off with the technician's name", "dn", -30);
      T("History of inspections for each bike", "dl", -24);
    S("Report and lock out a damaged bike", "pu", 3, "ops-agent");
      T("Rider can report damage in two taps", "dn", -8);
      T("Lock out a bike after two reports", "ph", 3);
      T("Damage report endpoint for operators", "ph", 4);
      T("Tell the nearest technician", "nn", 5);
      T("Tag a locked-out bike with its fault", "nn", 8);
      T("Unlock a bike again after the repair", "nn", 10);
    S("Battery swap schedule", "nn", 35);
      T("Plan the swaps for the day", "nn", 34);
      T("Alert at 20 percent charge", "nn", 35);
      T("Route for the battery van", "nl", 40);
    S("Spare parts stock", "nl", null);
      T("List of parts and where they are", "nl", null);
      T("Reorder level for each part", "nl", null);
    S("Workshop jobs and repair history for each bike", "pn", 18);
      T("Job card for each repair", "dn", -12);
      T("Repair history on the bike's page", "pn", 5);
      T("Time spent on each job", "nn", 12);
      T("Parts used on each job", "nn", 17);
      T("Workshop view on a tablet", "nn", 20);
    S("Tyre wear predictions", "xl", null);
      T("Wear model from the distance ridden", "xl", -15);
      T("Alert when a tyre is due", "xl", -10);
    S("Bike numbers painted on the frame", "nn", 12);
      T("Order paint stencils", "dn", -14);
      T("Check that every bike has a readable number", "pn", 6);
  E("Safety, incidents and insurance", "pu", 30);
    S("Report a crash or injury in the app", "pu", 12);
      T("Crash report form in the app", "pu", 6);
      T("Send the city a report within 24 hours", "nu", 10);
      T("Show first aid and emergency numbers", "dn", -20);
      T("Attach photos to a report", "nn", 14);
      T("Call the rider the next day", "nl", 16);
    S("Helmet reminders and partner discount", "nl", 80);
      T("Helmet partner order form", "nl", 70);
      T("Show a helmet reminder before the first ride", "nl", 75);
      T("Discount code at checkout", "nl", 80);
    S("Insurance terms riders must accept", "nh", 15);
      T("Insurance terms text from the insurer", "nh", -4);
      T("Rider accepts the terms before the first ride", "nh", 18);
    S("Lights check before an evening ride", "nn", null);
  E("Rider support", "nn", null);
    S("Help centre inside the app", "nn", 40);
      T("Write the first twenty articles", "nn", 36);
      T("Search inside the help centre", "nn", 38);
      T("Show help from the failed unlock screen", "nh", 40);
      T("Mark every article with the day it was last checked", "nl", 44);
    S("Support inbox for the team", "pn", 9);
      T("Shared inbox for support", "dn", -20);
      T("Tags for unlock, charge, bike and other", "pn", 4);
      T("Reply templates", "nn", 8);
      T("Weekly report of missed promises", "nn", 12);
    S("Chat with support", "nl", 120);
  E("City partnership and reporting", "ph", null);
    S("Monthly ridership report for the city", "nh", 50);
      T("Count rides by station and hour", "nn", 30);
      T("Report layout that the city asked for", "nn", 36);
      T("Send the report on the first of each month", "nn", 48);
      T("Check the numbers with the city once", "nn", 50);
    S("Open data feed of anonymous trips", "nn", 110);
      T("Define what an anonymous trip record holds", "nn", 90);
      T("Publish a daily file", "nn", 100);
      T("Leave out trips shorter than 60 seconds", "nl", 100);
    S("Station signs and permits", "pn", -12);
      T("Sign design agreed with the city", "dn", -30);
      T("Permits for 20 more stations", "ph", -20);
      T("Print and mount the station signs", "nn", -5);
  E("Notifications and messages", "nn", null);
    S("Push notifications for reservations and rides", "nn", 38);
      T("Push when a reservation is about to end", "nn", 36);
      T("Push when a ride has been open for two hours", "nn", 38);
      T("Settings to turn each kind off", "nn", 42);
    S("Send the rider an email receipt when a ride ends", "nn", 32);
    S("Quiet hours", "nl", null);
      T("Do not push between 22:00 and 07:00", "nl", null);
      T("Let riders choose their own hours", "nl", null);
    S("Message every rider at one station", "nn", 75);
      T("Pick the station on a map", "nn", 70);
      T("Write and preview the message", "nn", 72);
      T("Send in the language of each rider", "nn", 75);
  E("Rebalancing: moving bikes between stations", "nn", 150);
    S("Night-time moves between stations", "nn", null);
  E("Adaptive and cargo bikes pilot", "nl", null);

  /* -- links between work: [type, source, target]; "needs" reads "source needs target first" --------- */

  const links = [
    ['needs', 'T-41', 'T-40'], ['needs', 'T-42', 'T-41'], ['needs', 'T-44', 'T-42'], ['needs', 'T-47', 'T-42'],
    ['needs', 'T-43', 'T-59'], ['needs', 'T-62', 'T-61'], ['needs', 'T-62', 'T-87'], ['needs', 'T-88', 'T-87'],
    ['needs', 'T-90', 'T-103'], ['needs', 'T-89', 'T-86'], ['needs', 'T-25', 'T-24'], ['needs', 'T-26', 'T-24'],
    ['needs', 'T-10', 'T-8'], ['needs', 'T-7', 'T-8'], ['needs', 'T-20', 'T-19'], ['needs', 'T-29', 'T-28'],
    ['needs', 'T-73', 'T-70'], ['needs', 'T-74', 'T-73'], ['needs', 'T-78', 'T-77'], ['needs', 'T-79', 'T-78'],
    ['needs', 'T-79', 'T-100'], ['needs', 'T-99', 'T-97'], ['needs', 'T-93', 'T-92'], ['needs', 'T-100', 'T-87'],
    ['needs', 'T-154', 'T-153'], ['needs', 'T-146', 'T-145'], ['needs', 'S-19', 'S-16'], ['needs', 'S-22', 'S-21'],
    ['needs', 'S-23', 'S-21'], ['needs', 'S-53', 'S-27'],
    ['relates', 'T-61', 'T-127'], ['relates', 'S-15', 'S-20'], ['relates', 'S-27', 'S-30'], ['relates', 'S-39', 'S-41'],
    ['relates', 'S-33', 'S-14'], ['relates', 'S-2', 'S-18'],
    ['duplicates', 'S-24', 'S-50'],
    ['supersedes', 'T-18', 'T-17'],
  ];

  /* -- requirements: [id, status, story or null, title] (the board calls them requirements) ---------- */

  const rules = [
    [1, 'held', 'S-10', 'A scan opens the lock within two seconds on a good connection'],
    [2, 'settled', 'S-10', 'A used or expired code is refused with a clear reason'],
    [3, 'held', 'S-10', 'A rider can have only one open ride at a time'],
    [4, 'settled', 'S-11', 'The bike number on the frame is the number a rider types'],
    [5, 'held', 'S-12', 'A reservation lasts exactly ten minutes'],
    [6, 'held', 'S-12', 'A rider has at most one reservation at a time'],
    [7, 'held', 'S-17', 'A ride ends only when the lock is closed on a dock'],
    [8, 'settled', 'S-17', 'Ending a ride outside a station costs a flat fee'],
    [9, 'settled', 'S-18', 'Only the start and the end of a route are kept after 30 days'],
    [10, 'settled', 'S-21', 'No card number ever reaches our servers'],
    [11, 'held', 'S-21', 'The price is shown before the ride starts'],
    [12, 'held', 'S-21', 'A failed charge is retried for three days before the rider is asked'],
    [13, 'held', 'S-22', 'A pass covers the first 45 minutes of every ride'],
    [14, 'held', 'S-22', 'A pass renews on the same day each month'],
    [15, 'held', 'S-23', 'A refund goes back to the card that paid'],
    [16, 'settled', 'S-24', 'Every finished ride gets exactly one receipt'],
    [17, 'held', 'S-27', 'The map never shows a count older than one minute'],
    [18, 'held', 'S-27', 'A station with no free dock looks different from one with docks'],
    [19, 'held', 'S-33', 'Two rider reports lock a bike out until a technician has checked it'],
    [20, 'held', 'S-33', 'A locked-out bike is never offered on the map'],
    [21, 'settled', 'S-32', "Every inspection is recorded with the technician's name"],
    [22, 'held', 'S-39', 'A crash report reaches the city within 24 hours'],
    [23, 'held', 'S-41', 'A rider accepts the insurance terms before the first ride'],
    [24, 'settled', 'S-1', 'A sign-in code works once and expires after ten minutes'],
    [25, 'held', 'S-2', 'A rider can delete their account at any time'],
    [26, 'dropped', 'S-43', 'Every help article says when it was last checked'],
    [27, 'held', 'S-38', 'The number painted on the frame is easy to read'],
    [28, 'held', null, 'The app is available in at least three languages'],
    [29, 'held', null, 'The app works on a phone that is three years old'],
    [30, 'held', null, "Staff can export one rider's data when the rider asks"],
    [31, 'held', null, "Reports for the city use the city's own station names"],
    [32, 'held', null, 'Quiet hours never block a safety message'],
  ].map((r) => ({ id: r[0], status: r[1], story: r[2], title: r[3] }));

  /* -- decisions: the nine project-wide titles are shortened, the others are as recorded ------------ */

  const person = (by, why) => ({ by, why });
  const D = (id, title, long, by, ago, wide, on, rules, text, reason, extra) =>
    Object.assign(
      {
        id, title, long, by, decided: iso(-ago), wide, on, rules: rules || [], text, reason,
        state: 'in force', confirmation: null, replacedBy: null, replaces: null, changed: iso(-ago), ended: null,
      },
      extra || {},
    );

  const decisions = [
    D(1, 'Rider location kept only during a ride', 'Rider location is kept only while a ride is under way', 'Ines Duarte', 100, true,
      ['E-1', 'S-2', 'S-18'], [9],
      'A location point every ten seconds while a ride is open and at no other time; 30 days after a ride the route shrinks to its start and end.',
      'The data protection officer asked for the least location data that still answers a rider\'s complaint.',
      { confirmation: person('Ines Duarte', 'Agreed in the privacy review.') }),
    D(2, 'Money in whole cents', 'Money is stored in whole cents and shown in the rider\'s own currency format', 'Marcus Hale', 98, true,
      ['E-1', 'S-21', 'S-26'], [],
      'Every amount is a whole number of cents with a currency code; it is formatted for display only at the last step.',
      'Decimal sums produced receipts a cent off, which finance had to correct by hand.',
      { confirmation: person('Marcus Hale', 'Checked against the accounting export.') }),
    D(3, 'WCAG 2.2 level AA on every screen', 'Every screen meets WCAG 2.2 level AA', 'Priya Nair', 95, true,
      ['E-1', 'S-9'], [],
      'No screen ships before it meets WCAG 2.2 level AA, even the last one of a milestone.',
      'Riders include people with screen readers and one-handed riders, and the city contract names the standard.'),
    D(4, 'Times stored in UTC, shown in the station\'s zone', 'Times are stored in UTC and shown in the station\'s time zone', 'Lena Okafor', 92, true,
      ['E-1', 'S-16', 'S-22'], [],
      'Every timestamp is stored in UTC and shown in the time zone of the station concerned.',
      'Prices and passes follow the city\'s clock, not the phone\'s.'),
    D(5, 'Public API versioned, never breaks', 'The public API is versioned in the address and never breaks within a version', 'Lena Okafor', 90, true,
      ['E-1', 'S-3'], [],
      'Routes start with a version; within a version we only add. An old version keeps working for twelve months.',
      'Partners cannot update on the day we do.'),
    D(6, 'A bike is its frame number', 'A bike is identified by the number painted on its frame, never by its lock', 'Tomas Berg', 88, true,
      ['E-1', 'S-38', 'S-11'], [4],
      'The number painted on the frame is the only identifier people see or type; a lock serial is only an attribute.',
      'Technicians swap locks far more often than frames.'),
    D(7, 'Last two major iOS and Android versions', 'The apps support the last two major versions of iOS and Android', 'Lena Okafor', 85, true,
      ['E-1', 'T-47'], [29],
      'The apps are built and tested for the two newest major versions of each phone system.',
      'Supporting every old phone doubles testing time (see fact #12).'),
    D(8, 'First reply within one working day', 'Support promises a first reply within one working day', 'Kofi Mensah', 80, true,
      ['E-1', 'S-44'], [],
      'A person replies within one working day; unlock and charge problems within four working hours.',
      'Riders who waited two days stopped writing.'),
    D(9, 'No ads or outside analytics code', 'No advertising or third-party analytics code in the apps', 'Priya Nair', 75, true,
      ['E-1', 'S-2'], [],
      'The apps contain no advertising code and no third-party analytics libraries.',
      'The libraries we tried send the advertising identifier by default (see fact #17).',
      { confirmation: person('Priya Nair', 'Said so in the planning meeting.') }),
    D(10, 'Card details are entered only on the payment provider\'s page', null, 'Lena Okafor', 78, false,
      ['S-21', 'T-86'], [10],
      'Our apps and servers receive only a token that stands for the card, never the card number.',
      'It keeps card numbers out of our systems and avoids a much larger security review.',
      { confirmation: person('Marcus Hale', 'Matches the provider\'s agreement.') }),
    D(11, 'Unlock codes are signed, used once and expire after 30 seconds', null, 'Lena Okafor', 70, false,
      ['S-10', 'T-40'], [2],
      'The code is signed by the server, can be used once and expires after 30 seconds.',
      'A photograph of a code must not open a bike later.',
      { confirmation: null, history: 'Confirmed, then un-confirmed by Lena Okafor: the lifetime is under review after the field test.', changed: '2026-11-11' }),
    D(12, 'Single rides cost an unlock fee plus a price per minute', null, 'Marcus Hale', 65, false,
      ['S-26', 'S-21'], [],
      'A flat unlock fee plus a price for every minute after the first five.',
      'The pilot charged this way and finance found it easy to explain.',
      { state: 'replaced', replacedBy: 20, changed: '2026-10-29', ended: '2026-10-29' }),
    D(13, 'No weekly pass in the first release', null, 'Priya Nair', 50, false,
      ['S-22'], [],
      'The first release sells monthly and annual passes only.',
      'The survey found interest in a monthly pass (fact #5).'),
    D(14, 'Reservations hold a bike for ten minutes', null, 'Priya Nair', 60, false,
      ['S-12', 'S-28'], [5, 6],
      'A reservation holds one bike for exactly ten minutes, one reservation per rider.',
      'Ten minutes is the walk to the farthest station in the pilot area.',
      { confirmation: person('Priya Nair', 'Agreed with the operations team.') }),
    D(15, 'A bike with two damage reports is locked out until a technician checks it', null, 'Tomas Berg', 45, false,
      ['S-33', 'T-126'], [19, 20],
      'Two reports from two riders lock the bike out and remove it from the map until it is inspected.',
      'One report can be a mistake; two almost never are.',
      { confirmation: person('Tomas Berg', 'Agreed with the technicians.') }),
    D(16, 'Receipts are sent by text message as well as by email', null, 'Priya Nair', 40, false,
      ['S-24', 'S-50'], [],
      'Every receipt goes by email and also by text message.',
      'Some riders do not read their email.',
      { state: 'withdrawn', changed: '2026-11-10', ended: '2026-11-10', withdrawnWhy: 'The city\'s privacy review asked us to use email only, since a phone number is not otherwise needed.' }),
    D(17, 'Crash reports go to the city within 24 hours', null, 'Ines Duarte', 30, false,
      ['S-39', 'T-146'], [22],
      'Every crash or injury report goes to the city\'s transport office within 24 hours, weekends included.',
      'The partnership agreement requires it (fact #10).',
      { confirmation: person('Ines Duarte', 'Matches clause 9.2 of the agreement.') }),
    D(18, 'Bikes are moved between stations only between 1 and 5 in the morning', null, 'Tomas Berg', 25, false,
      ['S-53', 'E-11'], [],
      'Vans move bikes only between one and five in the morning.',
      'A van in a bike lane at rush hour blocks the riders it is meant to help (fact #13).'),
    D(19, 'The Bluetooth unlock is prototyped before it is designed', null, 'Lena Okafor', 12, false,
      ['S-15', 'T-63'], [],
      'Nobody designs the offline unlock until a prototype has opened a real lock over Bluetooth.',
      'The vendor promises five metres but nobody on the team has tried it (fact #3).'),
    D(20, 'Single rides cost a price per minute, with the first five minutes free', null, 'Marcus Hale', 20, false,
      [], [],
      'A single ride has no unlock fee; the first five minutes are free and each minute after that has one price.',
      'The unlock fee felt like a penalty for the short trips we want most. This replaces decision #12.',
      { replaces: 12, confirmation: person('Marcus Hale', 'Approved with the finance team.') }),
  ];

  /* -- facts: text and source as recorded; the dates are illustrative -------------------------------- */

  const F = (id, text, source, on, rules, decisionsFor, extra) =>
    Object.assign(
      { id, text, source, on, rules: rules || [], decisions: decisionsFor || [], state: 'in force', confirmation: null, replacedBy: null, replaces: null },
      extra || {},
    );

  const facts = [
    F(1, 'The city has 142 km of bike lanes, 38 km of them separated from traffic.', 'City transport office, open data download, spring edition.', ['E-5', 'S-30'], [], [], { recorded: '2026-07-28' }),
    F(2, 'The pilot fleet is 600 bikes across 48 stations, 120 of the bikes electric.', 'Operations plan, version 3, section 2.', ['E-6', 'E-5', 'S-34'], [], [], { recorded: '2026-07-30', confirmation: person('Tomas Berg', 'Counted at the depot.') }),
    F(3, 'The lock vendor says its locks can be opened over Bluetooth from up to 5 metres away.', 'Vendor datasheet, revision C, page 14.', ['S-15', 'T-63'], [], [19], { recorded: '2026-10-30' }),
    F(4, 'The payment provider pays out three days after a charge settles, and can reverse a disputed charge for 120 days.', 'Payment provider agreement, section 4.', ['S-21', 'S-23'], [], [], { recorded: '2026-08-28', confirmation: person('Marcus Hale', 'Read the agreement.') }),
    F(5, '62 of 100 survey respondents said they would pay for a monthly pass.', 'Rider survey, 410 respondents, February.', ['S-22'], [], [13], { recorded: '2026-09-20' }),
    F(6, 'The average pilot ride lasts 17 minutes.', 'Pilot dashboard export for the first month.', ['S-16', 'S-26'], [], [], { recorded: '2026-09-30', state: 'replaced', replacedBy: 19, changed: '2026-10-27' }),
    F(7, 'There is no mobile signal in the underground car park on Mill Street, where station 31 stands.', 'Field visit notes from a technician\'s round.', ['S-15', 'S-20'], [], [], { recorded: '2026-10-28' }),
    F(8, 'Swapping the battery of an electric bike takes about four minutes.', 'Timed during the workshop trial with two technicians.', ['S-34'], [], [], { recorded: '2026-11-02', changed: '2026-11-13', history: 'Confirmed, then un-confirmed by Tomas Berg: the new batteries are slower, so it has to be timed again.' }),
    F(9, 'The support inbox received about 90 messages a week during the pilot.', 'Support inbox report, weeks 1 to 8.', ['S-44', 'S-43'], [], [], { recorded: '2026-08-27', confirmation: person('Kofi Mensah', 'These are my own numbers.') }),
    F(10, 'The city requires a report within 24 hours of any injury on one of its bikes.', 'Partnership agreement, clause 9.2.', ['S-39', 'S-41'], [22], [17], { recorded: '2026-10-12', confirmation: person('Ines Duarte', 'I have the clause in front of me.') }),
    F(11, 'The helmet partner can deliver to 12 stations.', 'The partner\'s sales call.', ['S-40'], [], [], { recorded: '2026-10-05', state: 'withdrawn', changed: '2026-11-12', withdrawnWhy: 'The contract says 20 stations. The figure of 12 came from an early call and was wrong.' }),
    F(12, 'Phones running Android 9 or older make up 4 percent of riders.', 'App store analytics report for the last quarter.', ['T-47'], [29], [7], { recorded: '2026-08-20' }),
    F(13, 'Weekday demand peaks between 8:00 and 9:00 and between 17:00 and 18:30.', 'Pilot dashboard export, rides by hour.', ['E-11', 'S-53', 'S-27'], [], [18], { recorded: '2026-10-15' }),
    F(14, 'The city\'s data protection officer needs the impact assessment two weeks before the launch decision.', 'Email from the city\'s data protection officer.', ['E-1', 'S-2', 'T-11'], [], [], { recorded: '2026-09-10', confirmation: person('Ines Duarte', 'From the officer\'s own email.') }),
    F(15, 'The payment provider\'s hosted page is certified to the card industry\'s data security standard.', 'The provider\'s compliance page, read by Lena Okafor.', ['E-1', 'S-21'], [], [10], { recorded: '2026-08-28', confirmation: person('Lena Okafor', 'Checked on the provider\'s site.') }),
    F(16, 'The city allows rider data that is needed for accounting to be kept for up to 13 months.', 'Partnership agreement, schedule 3.', ['E-1', 'S-2'], [25], [1], { recorded: '2026-08-12' }),
    F(17, 'The two analytics libraries we tried send the phone\'s advertising identifier by default.', 'A test with a network proxy, written up by Lena Okafor.', ['E-1'], [], [9], { recorded: '2026-07-30' }),
    F(18, 'Tyres last about 3,000 km before they need replacing.', 'Workshop log for the pilot fleet.', ['S-37'], [], [], { recorded: '2026-10-01' }),
    F(19, 'The average pilot ride lasts 14 minutes once rides by staff are left out.', 'Pilot dashboard export for the first three months, staff rides removed.', ['S-16', 'S-26'], [], [], { recorded: '2026-10-27', replaces: 6, why: 'The first figure counted rides that staff made while testing.' }),
  ];
  facts.forEach((f) => { if (!f.changed) f.changed = f.recorded; });

  /* -- what the index finds alike: [a, b, score, note]. The first two are what the real board shows for S-1. */

  const similar = [
    ['S-1', 'S-48', 0.42, 'real'], ['S-1', 'S-52', 0.37, 'real'],
    ['T-61', 'T-125', 0.68], ['T-145', 'T-125', 0.66], ['T-92', 'T-103', 0.63], ['T-98', 'T-97', 0.61],
    ['S-14', 'T-43', 0.58], ['T-29', 'T-59', 0.47], ['S-43', 'T-157', 0.44], ['T-44', 'T-88', 0.45],
    ['T-100', 'S-50', 0.82], ['S-30', 'S-27', 0.47],
  ].map((s) => ({ a: s[0], b: s[1], score: s[2], real: s[3] === 'real' }));
  /* Pairs that are already marked by a link (duplicates, supersedes) are not "possible" any more. */

  /* References: the epic, story or task named in the text of another, as [[story:12]] in the demo's own texts
     (source names target). They are real: read from the demo's descriptions. */
  const mentions = 'E-1 S-1;E-1 S-2;E-1 S-3;E-1 E-4;S-1 S-2;E-2 S-38;E-2 T-40;E-2 T-42;E-2 T-43;E-2 S-15;E-3 S-17;E-3 S-15;E-4 S-25;E-4 T-106;E-4 S-23;E-4 S-24;E-6 S-32;E-6 S-33;E-6 S-34;E-6 S-38;E-7 S-39;E-7 S-41;E-7 S-40;E-7 S-33;E-8 S-43;E-10 S-24;E-10 S-50;E-11 S-27;E-12 E-2'
    .split(';').map((p) => p.split(' '));

  window.VRUM_DEMO = {
    project: 'Riverside Bike Share',
    today: TODAY,
    lastLooked: '2026-11-05',
    nodes, links, mentions, rules, decisions, facts, similar,
    people: ['Ines Duarte', 'Marcus Hale', 'Priya Nair', 'Lena Okafor', 'Tomas Berg', 'Kofi Mensah'],
    counts: { board: 233, graph: 311, roadmap: 223, activity: 5 },
  };
})();
