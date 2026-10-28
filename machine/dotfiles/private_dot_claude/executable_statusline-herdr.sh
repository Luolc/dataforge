#!/bin/sh
# Claude Code's statusLine command: one plain line out of the JSON it sends on stdin --
# directory, model, context used, 5-hour and weekly quota used, each quota followed by the time
# left until it resets (↻2h13m, ↻3d4h: the two largest of d/h/m; under a minute is ↻0m). A reset
# time that is missing or already past is left out. The context part is the tokens in the
# context window over the window size, then the percentage (ctx 412k/1M 41%); total_input_tokens
# is the same input + cache sum that used_percentage is computed from. One jq call, no node:
# every live session runs this on each refresh. Any number stdin does not carry (used_percentage
# is null and rate_limits absent until the session's first API response) is left out with its
# label when nothing of that part remains, rather than fetched from anywhere else.
exec jq -r --arg home "$HOME" '
  def left: (. - now) as $s | if $s <= 0 then ""
    else ($s / 60 | floor) as $m
      | if $m >= 1440 then " ↻\($m / 1440 | floor)d\($m % 1440 / 60 | floor)h"
        elif $m >= 60 then " ↻\($m / 60 | floor)h\($m % 60)m"
        else " ↻\($m)m" end end;
  def pct($label; $reset):
    if . == null then empty
    else "\($label) \(round)%" + ($reset | if type == "number" then left else "" end) end;
  def tok: if . >= 999500 then "\(. / 100000 | round / 10)M"
    elif . >= 1000 then "\(. / 1000 | round)k"
    else "\(round)" end;
  def ctx:
    [ (if (.total_input_tokens | type) != "number" then empty
       elif (.context_window_size | type) == "number"
       then "\(.total_input_tokens | tok)/\(.context_window_size | tok)"
       else .total_input_tokens | tok end),
      (.used_percentage | if type == "number" then "\(round)%" else empty end)
    ] | if length == 0 then empty else "ctx " + join(" ") end;
  [ (.workspace.current_dir // .cwd // empty
      | if . == $home or startswith($home + "/") then "~" + .[($home | length):] else . end),
    (.model.display_name // empty),
    (.context_window // {} | ctx),
    (.rate_limits.five_hour as $q | $q.used_percentage | pct("5h"; $q.resets_at)),
    (.rate_limits.seven_day as $q | $q.used_percentage | pct("7d"; $q.resets_at))
  ] | join(" · ")'
