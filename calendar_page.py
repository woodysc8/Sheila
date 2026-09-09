"""Small browser UI for Sheila's personal calendar."""


CALENDAR_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sheila Personal Calendar</title>
<style>
:root { color-scheme: light; font-family: Georgia, 'Times New Roman', serif; background: #f5f1e8; color: #25231f; }
body { margin: 0; padding: 2rem; }
main { max-width: 1100px; margin: 0 auto; }
header { display: flex; align-items: center; justify-content: space-between; gap: 1rem; margin-bottom: 1rem; }
h1 { margin: 0; font-size: 2rem; }
button, input, textarea { font: inherit; }
button { border: 1px solid #35322b; background: #fffdf8; padding: .55rem .8rem; cursor: pointer; }
button:hover { background: #e8dfce; }
.toolbar { display: flex; align-items: center; gap: .5rem; }
#month-title { min-width: 12rem; text-align: center; font-size: 1.2rem; }
.layout { display: grid; grid-template-columns: minmax(0, 1fr) 320px; gap: 1rem; align-items: start; }
.calendar { background: #fffdf8; border: 1px solid #c8bead; }
.weekdays, .days { display: grid; grid-template-columns: repeat(7, 1fr); }
.weekdays div { padding: .7rem .4rem; color: #6b6256; font-size: .85rem; border-bottom: 1px solid #ded5c7; }
.day { min-height: 7rem; padding: .45rem; border-right: 1px solid #ded5c7; border-bottom: 1px solid #ded5c7; }
.day:nth-child(7n) { border-right: 0; }
.day-number { color: #6b6256; font-size: .85rem; }
.day.outside { background: #f7f3eb; color: #aaa093; }
.event { display: block; width: 100%; margin-top: .35rem; padding: .3rem; text-align: left; border: 0; background: #d7e4e1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
form { background: #fffdf8; border: 1px solid #c8bead; padding: 1rem; }
label { display: block; margin: .7rem 0 .25rem; font-size: .9rem; }
input, textarea { box-sizing: border-box; width: 100%; border: 1px solid #bdb2a2; background: #fff; padding: .5rem; }
textarea { min-height: 5rem; resize: vertical; }
.form-actions { display: flex; gap: .5rem; margin-top: 1rem; }
#status { min-height: 1.4rem; margin: .8rem 0; color: #5a4b36; }
@media (max-width: 800px) { body { padding: 1rem; } .layout { grid-template-columns: 1fr; } .day { min-height: 5.5rem; } }
</style>
</head>
<body>
<main>
<header><h1>Sheila Personal Calendar</h1><div class="toolbar"><button id="previous" type="button">Previous</button><strong id="month-title"></strong><button id="next" type="button">Next</button></div></header>
<div id="status" role="status"></div>
<div class="layout">
<section class="calendar" aria-label="Personal calendar"><div class="weekdays"><div>Sun</div><div>Mon</div><div>Tue</div><div>Wed</div><div>Thu</div><div>Fri</div><div>Sat</div></div><div id="days" class="days"></div></section>
<form id="event-form"><h2 id="form-title">New event</h2><input id="event-id" type="hidden"><label for="title">Title</label><input id="title" required maxlength="200"><label for="start">Start</label><input id="start" type="datetime-local" required><label for="end">End</label><input id="end" type="datetime-local" required><label for="timezone">Timezone</label><input id="timezone" value="America/New_York" required><label for="location">Location</label><input id="location" maxlength="300"><label for="description">Notes</label><textarea id="description" maxlength="2000"></textarea><div class="form-actions"><button type="submit">Save event</button><button id="delete" type="button" hidden>Delete</button><button id="clear" type="button">Clear</button></div></form>
</div>
</main>
<script>
const state = { month: new Date(new Date().getFullYear(), new Date().getMonth(), 1), events: [] };
const $ = id => document.getElementById(id);
const pad = n => String(n).padStart(2, '0');
const dateKey = date => `${date.getFullYear()}-${pad(date.getMonth()+1)}-${pad(date.getDate())}`;
const monthLabel = new Intl.DateTimeFormat(undefined, { month: 'long', year: 'numeric' });
function localValue(date) { return `${date.getFullYear()}-${pad(date.getMonth()+1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`; }
function monthBounds() { const start = new Date(state.month); const end = new Date(start.getFullYear(), start.getMonth()+1, 1); return { start: `${dateKey(start)}T00:00:00`, end: `${dateKey(end)}T00:00:00` }; }
async function load() { const range = monthBounds(); const response = await fetch(`/api/calendar/events?start=${encodeURIComponent(range.start)}&end=${encodeURIComponent(range.end)}&timezone=America%2FNew_York`); if (!response.ok) throw new Error(await response.text()); state.events = (await response.json()).events; render(); }
function render() { $('month-title').textContent = monthLabel.format(state.month); const first = new Date(state.month.getFullYear(), state.month.getMonth(), 1); const gridStart = new Date(first); gridStart.setDate(first.getDate() - first.getDay()); const days = $('days'); days.innerHTML = ''; for (let i=0; i<42; i++) { const day = new Date(gridStart); day.setDate(gridStart.getDate()+i); const cell = document.createElement('div'); cell.className = `day${day.getMonth() === state.month.getMonth() ? '' : ' outside'}`; cell.innerHTML = `<div class="day-number">${day.getDate()}</div>`; state.events.filter(event => event.start.slice(0,10) === dateKey(day)).forEach(event => { const button = document.createElement('button'); button.className = 'event'; button.type = 'button'; button.textContent = `${event.start.slice(11,16)} ${event.title}`; button.onclick = () => editEvent(event); cell.appendChild(button); }); days.appendChild(cell); } }
function editEvent(event) { $('form-title').textContent = 'Edit event'; $('event-id').value = event.id; $('title').value = event.title; $('start').value = event.start.slice(0,16); $('end').value = event.end.slice(0,16); $('timezone').value = event.timezone; $('location').value = event.location; $('description').value = event.description; $('delete').hidden = false; }
function clearForm() { $('form-title').textContent = 'New event'; $('event-id').value = ''; $('event-form').reset(); $('timezone').value = 'America/New_York'; $('delete').hidden = true; }
$('previous').onclick = () => { state.month.setMonth(state.month.getMonth()-1); load().catch(showError); };
$('next').onclick = () => { state.month.setMonth(state.month.getMonth()+1); load().catch(showError); };
$('clear').onclick = clearForm;
$('delete').onclick = async () => { const id = $('event-id').value; if (!id || !confirm('Delete this event?')) return; const response = await fetch(`/api/calendar/events/${id}`, { method: 'DELETE' }); if (!response.ok) return showError(new Error(await response.text())); clearForm(); await load(); };
$('event-form').onsubmit = async event => { event.preventDefault(); const payload = { title: $('title').value, start: $('start').value, end: $('end').value, timezone: $('timezone').value, location: $('location').value, description: $('description').value }; const id = $('event-id').value; const response = await fetch(`/api/calendar/events${id ? '/' + id : ''}`, { method: id ? 'PATCH' : 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) }); if (!response.ok) return showError(new Error(await response.text())); clearForm(); await load(); };
function showError(error) { $('status').textContent = error.message; }
load().catch(showError);
</script>
</body>
</html>"""
