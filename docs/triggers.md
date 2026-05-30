# Triggers and event sources

> Canonical, copy-paste reference (source names, `match` keys, payload shapes)
> for widget authors is **[widgets.md §9](widgets.md)**. This page is the design
> rationale and response model; `widgets.md` wins on exact API.

A widget can watch for events and react. The governing principle:

> **The widget owns the trigger. The dashboard owns the response.**

A widget decides *what* to watch for and emits a fire event when its
condition is met. It does not decide whether that matters, how important
it is, or how the user is alerted — all of that is configured in the
dashboard by the user, per instance. The widget's contribution ends at
"this happened, here's some data about it."

## Event sources

A trigger listens to an event source. The dashboard provides a set of
built-in sources so widgets don't each reimplement them:

- **`dbus`** — subscribe to a D-Bus signal (e.g.
  `org.freedesktop.Notifications` for incoming desktop notifications,
  MPRIS for media, login1 for session events).
- **`process`** — fires when a process matching a pattern starts or
  stops. Backed by watching `/proc` (or `proc` connector events).
- **`window`** — fires when a window with a given WM_CLASS / title
  appears, closes, or focuses. Shares the lockdown daemon's X watch.
- **`file`** — fires on changes to a path (polls the path's mtime/size).
- **`timer`** — fires on an interval or at a clock time.
- **`command`** — runs a command on an interval; fires when its output
  changes or matches a condition.

A widget declares the sources it uses in its manifest:

```json
"event_sources": ["dbus", "process"]
```

and subscribes at runtime through `ctx`:

```python
async def start(self):
    await ctx.events.subscribe(
        source="process",
        match={"pattern": "gimp", "event": "start"},   # pattern is a regex
        handler=self._on_gimp_start,
    )
    await ctx.events.subscribe(
        source="dbus",
        match={"interface": "org.freedesktop.Notifications",
               "member": "Notify"},
        handler=self._on_notification,
    )

async def _on_gimp_start(self, event):
    # The widget only reports that its trigger fired, with a payload.
    # It does NOT decide visibility, presentation, or importance — the
    # dashboard does, per this instance's configured response.
    await ctx.fire(payload={"program": "gimp"})
```

The widget's job ends at "this happened, here's the data." The dashboard
looks up the user-configured response for this instance and renders it —
which may include revealing the widget, showing a toast, playing a
sound, switching tabs, or nothing at all. See "Responses" below.

Note there is no `severity` and no `ctx.host.notify` in widget code. A
widget that wants to surface something calls only `ctx.fire(...)`.
`ctx.host.layout.*` remains available for a widget that legitimately
needs to manage its *own* visibility directly (e.g. a widget that
collapses itself when idle), but attention/notification is always routed
through `ctx.fire` and the dashboard's response config.

## Responses (dashboard-owned, user-configured)

A widget that fires a trigger does **not** decide what happens next. It
does not even rank importance. It emits a fire event with an optional
**payload** (structured data — an email's sender, a launched program's
name) and stops there.

Everything about the response is configured **in the dashboard**, by the
user, attached to the specific trigger on the specific widget instance:

- whether it notifies at all
- which presentation(s): badge, toast, sound, flash, overlay, switch to
  this widget's tab, reveal a hidden widget
- any parameters: sound to play, toast text (which may template the
  payload, e.g. `New mail from {sender}`), how long it stays

This lives in the instance's settings, which live in the profile/tab. So
the **same trigger on two different tabs can produce entirely different
responses** — that's the point. A "new email" widget on the Work tab
might show a quiet badge; the same widget on a Focus tab might do
nothing; on a Night tab it might play a sound and switch tabs.

### Why the widget defines so little

The widget knows *when* something happened. It has no business deciding
how much you care or how you should be interrupted — that depends on
context (which tab, what you're doing) the widget can't see and the user
can. Pushing all response logic to the dashboard means:

- widget authors (and AI) write only the trigger, never presentation
- response behavior is consistent and lives in one configurable place
- the user retunes responses without touching widget code

### Fire event shape

What the widget emits:

```python
await ctx.fire(payload={"sender": "alice@example.com",
                        "subject": "lunch?"})
```

`ctx.fire` is the widget's only notification-related call. No severity,
no presentation. The dashboard looks up the response configured for this
instance's trigger and renders it, templating from `payload` as needed.

### Response config (per instance, in the profile)

Stored alongside the instance in the profile file:

```json
{
  "id": "inst_mail1",
  "widget_id": "mail-notify",
  "x": 0, "y": 0, "w": 2, "h": 1,
  "settings": { "...": "widget's own settings" },
  "response": {
    "badge": true,
    "toast": { "enabled": true, "text": "New mail from {sender}" },
    "sound": { "enabled": false },
    "flash": false,
    "overlay": false,
    "reveal": false,
    "switch_to_tab": false
  }
}
```

A global default response template in `config.yaml` seeds the response
config when a trigger-capable widget is first placed, so the user starts
from something sensible and edits down:

```yaml
default_response:
  badge: true
  toast: { enabled: true, text: "{title}" }
  sound: { enabled: false }
  flash: true
  overlay: false
  switch_to_tab: false
```

Precedence is simple and two-level: the instance's `response` block is
authoritative; the global `default_response` only supplies the starting
values at placement time. There is no per-widget-type layer.

The dashboard owns the *rendering* of every response — widgets never
draw their own toasts or play their own sounds. This keeps notification
behavior uniform no matter who wrote the widget.

## Trigger-driven visibility

Triggers pair naturally with widget visibility states (see
`docs/widgets.md`): a widget can be placed on the grid as **hidden until
triggered**, sitting dormant until its event fires. When it fires
(`ctx.fire`), revealing the widget is one of the responses the user can
configure for that instance — `reveal` is a response option, not
something the widget does to itself. The "pop up when a program
launches" example is exactly this: a `process` source + a hidden
visibility state + a response config with reveal (and optionally a
toast or tab-switch) enabled.

## Cost and safety notes

- **Triggers only fire for running tabs** — i.e. the selected tab or any
  tab the user has enabled (see `docs/layout.md`). A disabled tab's
  widgets aren't running, so their triggers are silent. On launch all
  tabs start disabled, so background triggers exist only for tabs the
  user explicitly enabled this session.
- Event subscriptions are torn down automatically when the widget
  instance stops (including when its tab is disabled); widgets don't have
  to unsubscribe in `stop()` (though they may).
- Each subscription runs its own lightweight loop: `timer`/`file` are
  in-process; `process`/`window`/`command` poll a command at a modest
  interval (configurable via `match["interval"]`); `dbus` streams from a
  single persistent `dbus-monitor`. Keep intervals sensible — a panel runs
  for hours.
- There is no built-in fire rate-limiting yet: if your trigger could match
  rapidly, debounce inside your handler before calling `ctx.fire`.
