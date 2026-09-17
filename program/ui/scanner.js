/* wallpaper-scanner -- what the pointer is over, and the messages that result.
 *
 * The split is the same one harbor and arbor use: this file knows what the
 * pointer is over, because it owns the DOM; scanner.py knows what to do about
 * it, because it is the only side that can talk to the window manager or the
 * disk.
 *
 * Every control also reports itself to the host's log. The session that wrote
 * this cannot see the screen, so "the button fired" has to be a line in a
 * file rather than something anybody watched happen.
 */

'use strict';

/* The one door to the host.
 *
 * It used to be WebKit's message handlers, which exist only in WebKit. The
 * host is Qt now -- so that the same program runs on Windows, where WebKit's
 * GTK build does not exist at all -- and Qt hands the page a single object
 * over a web channel instead of a handler per name. The channel name travels
 * as an argument.
 *
 * `_wallscan_send` is installed by the host before this file runs, and queues
 * anything sent before the channel has finished connecting. That gap is real:
 * this script runs first, and a button pressed inside it would otherwise
 * vanish with nothing said. Everything below this line is unchanged, which is
 * the point of there only ever having been one call site. */
const send = (channel, payload) =>
  window._wallscan_send(channel, payload === undefined ? '' : payload);

const note = (what) => send('log', what);

const frame   = document.getElementById('frame');
const results = document.getElementById('results');
const status  = document.getElementById('status');
const stopbtn = document.getElementById('stopbtn');
const submitbtn = document.getElementById('submitbtn');
const confirm = document.getElementById('confirm');
const confirmtext = document.getElementById('confirmtext');
const howmany = document.getElementById('howmany');

let running = false;
const picked = new Set();

/* Following the feed.
 *
 * While the grid is pinned to its bottom, each new preview scrolls it down, so
 * results can be watched going past rather than hunted for. The moment he
 * scrolls up -- to look at one, or to tick it -- following stops, because a
 * grid that yanks itself back down under a pointer is a grid you cannot click
 * in. Scrolling back to the bottom picks the feed up again where it now is.
 *
 * A threshold rather than an exact match: scroll positions land on fractional
 * pixels at this DPI, and `scrollTop === scrollHeight - clientHeight` is a
 * test that fails at rest. */
const NEAR_BOTTOM = 48;

/* Whether the grid is currently pinned to its bottom, measured now rather
 * than remembered.
 *
 * It used to be a flag kept up to date by the scroll handler, and that flag
 * could be a frame out of date at exactly the wrong moment. A scroll event
 * does not fire the instant scrollTop is assigned -- it arrives before the
 * next paint -- so a preview landing in that gap read the stale flag, scrolled
 * the grid back to the bottom, and the scroll handler then agreed with the
 * position it had just been moved to. The effect was a grid that would not let
 * go when you scrolled up: seen about one run in three in the layout check,
 * which is what the check is for. Asking the element where it is cannot go
 * stale. */
const atBottom = () =>
  results.scrollHeight - results.scrollTop - results.clientHeight <= NEAR_BOTTOM;

/* ---------- moving and resizing the window ---------- */

/* A drag anywhere that is not a control moves the window. Checked against the
 * real event target rather than a class on the surface, so a control added
 * later is not silently draggable. */
document.addEventListener('mousedown', (e) => {
  if (e.button !== 0) return;
  if (e.target.closest('button, input, .grip, .list, #results, #confirm')) return;
  send('drag');
});

document.querySelectorAll('.grip').forEach((grip) => {
  grip.addEventListener('mousedown', (e) => {
    if (e.button !== 0) return;
    e.stopPropagation();
    send('resize', grip.dataset.edge);
  });
});

/* WebKit's own context menu is refused by the host as well; this stops the
 * page-level one so a right-click does nothing rather than something ugly. */
document.addEventListener('contextmenu', (e) => e.preventDefault());

/* ---------- stage one: sources and themes ---------- */

/* Built from what the host hands over rather than written into the markup:
 * phase two replaces the theme list with one pulled from each site's own
 * settings, and that must not mean editing the page. */
const TICK =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"' +
  ' stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7"/></svg>';

/* A source card: the name, what it is, and what it needs before it will work.
 *
 * The host decides what is on this list. Anything that could not be made to
 * work without editing scanner.py is not sent here at all -- not greyed out,
 * not explained, absent -- so nothing on this panel is a dead end. A card that
 * is merely waiting for a key still arrives, because a key is something a
 * person can go and get. */
window.scanner_setSources = (list) => {
  const box = document.getElementById('sourcelist');
  box.innerHTML = '';
  list.forEach((s) => {
    const b = document.createElement('button');
    b.className = 'source' + (s.ready ? ' on' : '');
    b.dataset.id = s.id;

    const box_ = document.createElement('span');
    box_.className = 'box';
    box_.innerHTML = TICK;

    const body = document.createElement('span');
    body.className = 'body';

    const name = document.createElement('span');
    name.className = 'name';
    name.textContent = s.name;
    body.appendChild(name);

    if (s.what) {
      const what = document.createElement('span');
      what.className = 'what';
      what.textContent = s.what;
      body.appendChild(document.createElement('br'));
      body.appendChild(what);
    }

    /* What it needs, always said -- "Nothing at all" is the most useful thing
     * a source can say and it used to say nothing at all instead. */
    if (s.needs) {
      const needs = document.createElement('span');
      needs.className = 'needs';
      needs.textContent = s.ready ? s.needs : '⚠ ' + s.needs;
      body.appendChild(document.createElement('br'));
      body.appendChild(needs);
    }

    b.appendChild(box_);
    b.appendChild(body);

    if (!s.ready) {
      b.disabled = true;
    } else {
      b.addEventListener('click', () => {
        b.classList.toggle('on');
        note('source ' + s.id + ' ' + (b.classList.contains('on') ? 'on' : 'off'));
      });
    }
    box.appendChild(b);
  });
};

window.scanner_setThemes = (list) => {
  const box = document.getElementById('themelist');
  box.innerHTML = '';
  list.forEach((name) => {
    const b = document.createElement('button');
    b.className = 'pick';
    b.textContent = name;
    b.addEventListener('click', () => {
      b.classList.toggle('on');
      note('theme ' + name + ' ' + (b.classList.contains('on') ? 'on' : 'off'));
    });
    box.appendChild(b);
  });
};

/* ---------- the two dropdowns ---------- */

/* One dropdown, built twice: once for the sizes and once for the colours.
 *
 * Not a <select>. A native one draws its open list with the operating
 * system's own widget -- a light list in a dark window on Linux, and nothing
 * this stylesheet can reach on either system. That is the same reason this
 * program refuses the browser's context menu and never uses a `title`
 * attribute: a control that cannot be styled cannot be made to match.
 *
 * Closing on any click elsewhere is handled once, at the bottom, rather than
 * per menu. */
const makeDrop = (dropId, menuId, options, chosenId, onPick) => {
  const drop = document.getElementById(dropId);
  const menu = document.getElementById(menuId);
  menu.innerHTML = '';

  const close = () => { drop.classList.remove('open'); menu.hidden = true; };

  options.forEach((o) => {
    const b = document.createElement('button');
    b.className = 'opt' + (o.id === chosenId ? ' on' : '');
    b.dataset.id = o.id;
    if (o.hex !== undefined) {
      const dot = document.createElement('span');
      dot.className = 'dot';
      if (o.hex) dot.style.background = o.hex;
      else dot.dataset.any = '1';
      b.appendChild(dot);
    }
    const label = document.createElement('span');
    label.textContent = o.name;
    b.appendChild(label);
    b.addEventListener('click', () => {
      [...menu.querySelectorAll('.opt')].forEach((x) => x.classList.remove('on'));
      b.classList.add('on');
      close();
      onPick(o);
    });
    menu.appendChild(b);
  });

  const button = drop.querySelector('.dropbtn');
  button.addEventListener('click', (e) => {
    e.stopPropagation();
    const opening = menu.hidden;
    closeAllDrops();
    if (opening) {
      drop.classList.add('open');
      menu.hidden = false;
      const on = menu.querySelector('.opt.on');
      if (on) on.scrollIntoView({ block: 'nearest' });
    }
  });
};

const closeAllDrops = () => {
  [...document.querySelectorAll('.drop')].forEach((d) => {
    d.classList.remove('open');
    const m = d.querySelector('.dropmenu');
    if (m) m.hidden = true;
  });
};

document.addEventListener('click', closeAllDrops);
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') closeAllDrops();
});

/* ---- the minimum size ---- */

let chosenSize = '';
/* Only meaningful while chosenSize is 'exact'. Kept out here so that going
 * away to another size and coming back does not lose what was typed. */
let exactW = 3440;
let exactH = 1440;

const sizePanel = document.getElementById('sizepanel');
const exactwBox = document.getElementById('exactw');
const exacthBox = document.getElementById('exacth');

/* Digits only, the same rule as the how-many box. */
[exactwBox, exacthBox].forEach((box) => {
  box.addEventListener('input', () => {
    const cleaned = box.value.replace(/[^0-9]/g, '');
    if (cleaned !== box.value) box.value = cleaned;
  });
});

window.scanner_setSizes = (list, initial) => {
  chosenSize = initial;
  const label = document.getElementById('sizelabel');
  const start = list.find((z) => z.id === initial) || list[0];
  label.textContent = start.name;

  /* What the label says once a specific size is in force. The host writes the
   * same sentence its own way for the log; this one is for the button. */
  const exactLabel = () => exactW + ' × ' + exactH + ' exactly';

  const useExact = () => {
    exactW = Math.max(1, parseInt(exactwBox.value, 10) || 1);
    exactH = Math.max(1, parseInt(exacthBox.value, 10) || 1);
    chosenSize = 'exact';
    label.textContent = exactLabel();
    sizePanel.hidden = true;
    note('specific size ' + exactW + 'x' + exactH);
  };

  document.getElementById('sizeok').addEventListener('click', useExact);
  [exactwBox, exacthBox].forEach((box) => {
    box.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') useExact();
      if (e.key === 'Escape') document.getElementById('sizecancel').click();
    });
  });

  /* Backing out leaves the size as it was before the panel opened, rather
   * than half-applying a number nobody confirmed. */
  document.getElementById('sizecancel').addEventListener('click', () => {
    sizePanel.hidden = true;
    const back = list.find((z) => z.id === chosenSize);
    label.textContent = chosenSize === 'exact' ? exactLabel()
                      : (back ? back.name : start.name);
    note('specific size cancelled, still ' + label.textContent);
  });

  makeDrop('sizedrop', 'sizemenu', list, initial, (o) => {
    if (o.exact) {
      /* The dropdown cannot answer this one -- it needs two numbers -- so it
       * opens the panel that can, and nothing is chosen until that panel is
       * confirmed. */
      exactwBox.value = exactW;
      exacthBox.value = exactH;
      sizePanel.hidden = false;
      exactwBox.focus();
      exactwBox.select();
      note('specific size asked for');
      return;
    }
    chosenSize = o.id;
    label.textContent = o.name;
    note('minimum size ' + o.id);
  });
};

/* ---- the main colour ----
 *
 * One colour at a time, not several. He described this as picking THE main
 * colour -- "if he wants something white, he picks white" -- and two main
 * colours is not a thing a picture has. "Any colour" is the first entry and
 * the way back out, so there is always one without hunting for it. */

let chosenColourHex = '';

window.scanner_setColours = (list) => {
  const dot = document.getElementById('colourdot');
  const label = document.getElementById('colourlabel');
  const options = [{ id: '', name: 'Any colour', hex: '' }].concat(
    list.map((c) => ({ id: c.hex, name: c.name, hex: c.hex })));

  makeDrop('colourdrop', 'colourmenu', options, '', (o) => {
    chosenColourHex = o.hex;
    label.textContent = o.name;
    if (o.hex) {
      dot.style.background = o.hex;
      delete dot.dataset.any;
      note('colour ' + o.name + ' (' + o.hex + ')');
    } else {
      dot.style.background = '';
      dot.dataset.any = '1';
      note('colour cleared');
    }
  });
};

const chosenColour = () => chosenColourHex;

/* What is ticked in a list. Sources are cards and themes are pills, so both
 * class names are asked for: reading only one of them is how this quietly
 * sent a search with no sources at all the moment the source pills became
 * cards. Caught by the self-test, which is what it is for. */
const chosen = (selector) =>
  [...document.querySelectorAll(selector + ' .pick.on, ' + selector + ' .source.on')]
    .map((b) => b.dataset.id ||
                (b.querySelector('.name') || b).textContent.trim());

document.getElementById('searchbtn').addEventListener('click', () => {
  const typed = document.getElementById('themesearch').value.trim();
  send('search', JSON.stringify({
    sources: chosen('#sourcelist'),
    themes: chosen('#themelist'),
    /* What he typed travels beside the ticked pills rather than instead of
     * them. The host glues both into one search string, so a typed word and a
     * ticked theme reach every source together. */
    typed: typed,
    colour: chosenColour(),
    size: chosenSize,
    /* Only read by the host when size is 'exact'; sent always, because a
     * payload whose shape changes with a setting is a payload to get wrong. */
    width: exactW,
    height: exactH,
  }));
});

/* The ? is one of the window's own buttons and behaves like them -- same
 * size, same hover, same strip. What it opens is deliberately short. */
const helpPanel = document.getElementById('help');
document.getElementById('barhelp').addEventListener('click', () => {
  helpPanel.hidden = false;
  note('help opened');
});
document.getElementById('helpclose').addEventListener('click', () => {
  helpPanel.hidden = true;
  note('help closed');
});

document.getElementById('barclose').addEventListener('click', () => send('close'));
document.getElementById('barmin').addEventListener('click', () => send('minimize'));
document.getElementById('barmax').addEventListener('click', () => send('maxtoggle'));

/* ---------- stage two: how many ---------- */

/* Digits only, and never remembered: the box is rebuilt at 10 every time the
 * stage opens, at his instruction. No ceiling -- his words were that he would
 * stop it long before a silly number finished. */
howmany.addEventListener('input', () => {
  const cleaned = howmany.value.replace(/[^0-9]/g, '');
  if (cleaned !== howmany.value) howmany.value = cleaned;
});

const confirmNumber = () => {
  const n = Math.max(1, parseInt(howmany.value, 10) || 10);
  send('howmany', JSON.stringify(n));
};

document.getElementById('askgo').addEventListener('click', confirmNumber);
document.getElementById('askcancel').addEventListener('click', () => send('cancelask'));
howmany.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') confirmNumber();
  if (e.key === 'Escape') send('cancelask');
});

/* ---------- stage three: the grid ---------- */

window.scanner_reset = (want) => {
  results.innerHTML = '';
  picked.clear();
  results.scrollTop = 0;
  /* A new search starts at the top of an empty grid, which is also its
   * bottom, so following resumes by itself. */
  running = true;
  paint();
  status.textContent = 'starting — 0/' + want;
};

window.scanner_setCount = (source, found, want) => {
  status.textContent = source + ' — ' + found + '/' + want;
};

/* A whole line, for the things a count cannot say: finished, ran out, could
 * not be reached. He cannot see this program's log, so a search that dies
 * quietly has to say so on the strip or it looks like a slow one. */
window.scanner_setStatus = (text) => { status.textContent = text; };

/* One preview, appended where it belongs. Appending rather than rebuilding is
 * what makes the grid fill in place: a rebuild would restart every card's
 * animation and lose the scroll position on every arrival. */
window.scanner_addResult = (r) => {
  const card = document.createElement('div');
  card.className = 'card' + (r.have ? ' have' : '');
  card.dataset.id = r.id;

  const img = document.createElement('img');
  img.src = r.url;
  img.alt = '';
  card.appendChild(img);

  const res = document.createElement('span');
  res.className = 'res';
  res.textContent = r.w + '×' + r.h;
  card.appendChild(res);

  const src = document.createElement('span');
  src.className = 'src';
  src.textContent = r.source;
  card.appendChild(src);

  const tick = document.createElement('span');
  tick.className = 'tick';
  tick.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"' +
    ' stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M5 13l4 4L19 7"/></svg>';
  card.appendChild(tick);

  /* The whole card takes the click, not just the little box. The box is the
   * thing that shows the state; aiming at 26 pixels to select a wallpaper
   * would be the wrong kind of precise. */
  card.addEventListener('click', () => {
    card.classList.toggle('on');
    const on = card.classList.contains('on');
    on ? picked.add(r.id) : picked.delete(r.id);
    note('tick ' + r.id + ' ' + (on ? 'on' : 'off') + ', ' + picked.size + ' selected');
    paint();
  });

  /* Measured before the card goes in: afterwards the content is taller and
   * every grid looks scrolled-up. */
  const wasAtBottom = atBottom();
  results.appendChild(card);

  /* Instant, not smooth: previews land in bursts, and queued smooth scrolls
   * fight each other and arrive late. Three appends in four move nothing
   * anyway -- the content only grows when a new row of four starts -- so this
   * reads as a steady creep rather than a jump. */
  if (wasAtBottom) results.scrollTop = results.scrollHeight;
};

/* Submit is absent while the search runs, at his instruction -- not merely
 * disabled. Both are enforced: hidden here, and the click refused below, so a
 * stray Enter on a focused button cannot submit mid-search either. */
function paint() {
  stopbtn.textContent = running ? 'Stop' : 'Resume';
  stopbtn.classList.toggle('resume', !running);
  submitbtn.hidden = running;
  submitbtn.disabled = running || picked.size === 0;
  submitbtn.textContent = picked.size
    ? 'Submit ' + picked.size + ' selection' + (picked.size === 1 ? '' : 's')
    : 'Submit selections';
}

window.scanner_setRunning = (on) => { running = on; paint(); };
window.scanner_setRolled = (on) => frame.classList.toggle('rolled', on);

stopbtn.addEventListener('click', () => send('stopresume'));
document.getElementById('rollbtn').addEventListener('click', () => send('rolltoggle'));

/* Where the wallpapers go is asked on every save.
 *
 * It is the one thing this program does outside its own folder, so it is a
 * question rather than an assumption -- and the answer is remembered by the
 * host, which is what keeps the question cheap: the usual reply is to press
 * Save. The path shown is the host's, never one this page made up. */
const savePanel = document.getElementById('savepanel');
const saveDirLabel = document.getElementById('savedirlabel');
let saveDirPath = '';

window.scanner_setSaveDir = (shown, full) => {
  saveDirPath = full;
  saveDirLabel.textContent = shown;
};

submitbtn.addEventListener('click', () => {
  if (running) { note('submit refused: still searching'); return; }
  if (!picked.size) { note('submit refused: nothing selected'); return; }
  document.getElementById('savetext').textContent =
    'Save ' + picked.size + ' wallpaper' + (picked.size === 1 ? '' : 's') + ' to:';
  savePanel.hidden = false;
  note('save panel opened for ' + picked.size);
});

/* The chooser is the host's, because a folder chooser is the system's own job
 * and every one written in a web page is worse than the one already there. */
document.getElementById('savedirbtn').addEventListener('click', () => {
  send('choosedir');
  note('folder chooser asked for');
});

document.getElementById('savecancel').addEventListener('click', () => {
  savePanel.hidden = true;
  note('save cancelled, nothing downloaded');
});

document.getElementById('saveconfirm').addEventListener('click', () => {
  savePanel.hidden = true;
  send('submit', JSON.stringify({ picks: [...picked], dir: saveDirPath }));
  note('save confirmed into ' + saveDirPath);
});

window.scanner_submitted = (n) => {
  status.textContent = n + ' sent to be downloaded';
};

/* Once they are on disk the ticks are stale: leaving them lit invites a second
 * submit that would find every file already there and look like a failure. */
/* Saving is the one thing here that takes real time -- a full wallpaper is
 * megabytes, not kilobytes. The page is told when it is over so the self-test
 * can wait for it rather than guess, and so anything added later has a hook. */
window.scanner_saved = (saved, existing, failed) => {
  window.__lastSave = { saved, existing, failed };
};

window.scanner_clearPicks = () => {
  picked.clear();
  [...results.querySelectorAll('.card.on')].forEach((c) => {
    c.classList.remove('on');
    c.classList.add('have');
  });
  paint();
};

/* ---------- closing the results, which throws the previews away ---------- */

document.getElementById('gridclose').addEventListener('click', () => send('askdiscard'));

/* One confirm panel, asked several questions. The Yes carries whatever the
 * asker wanted done rather than one hardcoded action, so a second thing that
 * needs confirming does not mean a second panel to keep in step with this one. */
let confirmYes = null;
let confirmNo = null;

const askConfirm = (text, onYes, onNo) => {
  confirmtext.textContent = text;
  confirmYes = onYes;
  confirmNo = onNo || null;
  confirm.hidden = false;
};

window.scanner_confirmDiscard = (wasRunning) => {
  askConfirm(
    wasRunning ? 'Stop search and delete thumbnails?' : 'Delete thumbnails?',
    () => send('discard'),
    () => send('keepgoing'));
};
window.scanner_confirmClosed = () => { confirm.hidden = true; };

document.getElementById('confirmno').addEventListener('click', () => {
  confirm.hidden = true;
  const fn = confirmNo; confirmYes = confirmNo = null;
  if (fn) fn();
});
document.getElementById('confirmyes').addEventListener('click', () => {
  confirm.hidden = true;
  const fn = confirmYes; confirmYes = confirmNo = null;
  if (fn) fn();
});

/* --- forgetting what has been seen ---
 *
 * The record stops wallpapers he has already looked at coming back. This is
 * how he changes his mind about that: after four or five scans he may feel he
 * passed over something too quickly, and without this there is no way back to
 * it short of deleting a file he should not have to know about.
 *
 * It does not touch the within-run duplicate guard, which is a different
 * thing: that stops one scan showing the same wallpaper twice, and there is no
 * version of "show me more" that wants it off. Nor does it touch what is
 * already in his wallpaper folder -- that is read off the filenames every
 * launch, so there is nothing there to clear. */
const forgetbtn = document.getElementById('forgetbtn');

window.scanner_setSeenCount = (n) => {
  forgetbtn.disabled = n === 0;
  forgetbtn.textContent = n
    ? "Forget the " + n + " I've seen"
    : "Nothing seen yet";
};

forgetbtn.addEventListener('click', () => {
  if (forgetbtn.disabled) return;
  /* The question and nothing else. What happens next -- that they start
   * coming back, that his own folder stays excluded -- is what the button
   * says it does, and saying it twice made the box the size of a page. */
  askConfirm(forgetbtn.textContent + '?', () => send('forget'));
});

/* ---------- the host moving between stages ---------- */

window.scanner_setStage = (stage) => {
  frame.dataset.stage = stage;
  frame.classList.remove('rolled');
  confirm.hidden = true;
  savePanel.hidden = true;
  helpPanel.hidden = true;
  sizePanel.hidden = true;
  closeAllDrops();
  if (stage === 'ask') {
    howmany.value = '10';        /* never remembered, at his instruction */
    howmany.focus();
    howmany.select();
  }
  note('stage ' + stage);
};

note('page ready');

/* ---------- the self-test ----------
 *
 * Runs only when the host was started with WALLSCAN_SELFTEST=1. It presses
 * this page's own buttons, in the order a person would, and lets the host log
 * what each one did.
 *
 * It exists because the session that wrote this program cannot see the screen
 * and is not allowed to touch the pointer, so "every control fires" would
 * otherwise be a claim with nothing behind it. It clicks real elements and
 * goes through the real handlers -- a test that called the handlers directly
 * would pass with the buttons unwired.
 */

const SELFTEST = new URLSearchParams(location.search).get('selftest');

if (SELFTEST === '1' || SELFTEST === 'live') {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));

  /* Wait for a condition rather than a fixed delay: the host owns the stage
   * changes, and a fixed delay would race it on a slow start and pass on a
   * fast one for the wrong reason. */
  const until = async (label, test, tries = 400) => {
    for (let i = 0; i < tries; i++) {
      if (test()) return true;
      await wait(100);
    }
    note('SELFTEST STUCK waiting for ' + label);
    return false;
  };

  const press = (el, what) => {
    if (!el) { note('SELFTEST MISSING ' + what); return false; }
    el.click();
    note('selftest pressed ' + what);
    return true;
  };

  const fails_forget = [];

  (async () => {
    await until('sources', () => document.querySelectorAll('#sourcelist .source').length);
    note('selftest begins');

    /* The bug he reported: the close square floated in the corner and landed
       on top of the Search button, so Search could not be pressed. Overlap is
       a rectangle test, which is something this session can do without eyes. */
    const rect = (id) => document.getElementById(id).getBoundingClientRect();
    const hits = (a, b) => !(a.right <= b.left || a.left >= b.right ||
                             a.bottom <= b.top || a.top >= b.bottom);
    for (const id of ['barmin', 'barmax', 'barclose']) {
      if (!document.getElementById(id)) fails_forget.push('no ' + id + ' button');
    }
    const search = rect('searchbtn');
    for (const id of ['barmin', 'barmax', 'barclose']) {
      if (document.getElementById(id) && hits(rect(id), search)) {
        fails_forget.push(id + ' overlaps Search');
      }
    }
    if (hits(rect('forgetbtn'), search)) fails_forget.push('Forget overlaps Search');
    note('BAR search=' + Math.round(search.width) + 'x' + Math.round(search.height) +
         ' close=' + Math.round(rect('barclose').width) + 'px' +
         ' overlaps=' + fails_forget.length);

    /* Stage one. Turn one ready source off and back on, pick two themes. */
    const sources = [...document.querySelectorAll('#sourcelist .source:not([disabled])')];
    const sourceName = (el) => el.querySelector('.name').textContent;
    press(sources[1], 'source off (' + sourceName(sources[1]) + ')');
    press(sources[1], 'source back on');
    /* Every source says what it needs, including the ones that need nothing.
     * A card with no such line is a card a friend has to guess at. */
    const mute = [...document.querySelectorAll('#sourcelist .source')]
      .filter((el) => !el.querySelector('.needs'));
    if (mute.length) {
      fails_forget.push(mute.length + ' source(s) do not say what they need');
    }
    /* Every source visible without scrolling. A panel that hides half its
     * sources behind a scroll nobody expects is the same as not listing them,
     * and that is exactly what happened when the pills became cards: two of
     * four on screen at the height the window had always opened at. */
    const slist = document.getElementById('sourcelist');
    if (slist.scrollHeight > slist.clientHeight + 1) {
      fails_forget.push('the sources do not fit: ' + slist.scrollHeight +
                        ' inside ' + slist.clientHeight);
    }
    const themes = [...document.querySelectorAll('#themelist .pick')];
    press(themes[0], 'theme ' + themes[0].textContent);
    press(themes[3], 'theme ' + themes[3].textContent);

    /* The two new controls, exercised the same way as the rest: this
     * program's second definition of done is that every control writes a line
     * proving it fired, and a search bar nobody typed into and a swatch
     * nobody pressed would satisfy that by looking untouched. */
    const bar = document.getElementById('themesearch');
    bar.value = 'selftest typed this';
    bar.dispatchEvent(new Event('input'));
    note('selftest typed "' + bar.value + '" into the theme search bar');
    if (bar.getBoundingClientRect().bottom >
        document.querySelector('#themepane .list').getBoundingClientRect().top + 1) {
      fails_forget.push('theme search bar overlaps the theme pills');
    }

    /* The ? -- one of the window's own buttons, so it is pressed like the
     * rest rather than taken on trust. */
    press(document.getElementById('barhelp'), 'the ? button');
    if (document.getElementById('help').hidden) {
      fails_forget.push('the ? did not open the help panel');
    }
    press(document.getElementById('helpclose'), 'CLOSE on the help panel');

    /* Both dropdowns. Opened, counted, chosen from -- a menu that opens and a
     * menu that answers are different things, and only the second one is
     * worth anything. */
    const pane = document.getElementById('choicepane');
    if (pane.scrollHeight > pane.clientHeight + 1) {
      fails_forget.push('the size/colour strip is clipped: ' + pane.scrollHeight +
                        ' inside ' + pane.clientHeight);
    }

    press(document.getElementById('sizebtn'), 'the size dropdown open');
    const sizes = [...document.querySelectorAll('#sizemenu .opt')];
    note('selftest sees ' + sizes.length + ' sizes');
    if (document.getElementById('sizemenu').hidden) {
      fails_forget.push('the size dropdown did not open');
    }
    /* Opening upward is the whole reason these menus are hand-built: this
     * strip is at the bottom of the window, and a menu drawn below it would
     * be drawn outside the window. */
    const smenu = document.getElementById('sizemenu').getBoundingClientRect();
    const sbtn = document.getElementById('sizebtn').getBoundingClientRect();
    if (smenu.top >= sbtn.top) {
      fails_forget.push('the size menu opens downward, out of the window');
    }
    /* The specific size, opened and used, then put back to a floor so the
     * rest of the run searches the way it always has. A banner size left in
     * force would make every later assertion about results a test of
     * 1920x480 rather than of the program. */
    press(sizes.find((o) => o.dataset.id === 'exact'), 'Specific size…');
    if (document.getElementById('sizepanel').hidden) {
      fails_forget.push('Specific size did not open its panel');
    }
    document.getElementById('exactw').value = '3440';
    document.getElementById('exactw').dispatchEvent(new Event('input'));
    document.getElementById('exacth').value = '1440';
    document.getElementById('exacth').dispatchEvent(new Event('input'));
    press(document.getElementById('sizeok'), 'USE THIS SIZE 3440x1440');
    note('selftest sees the size button reading "' +
         document.getElementById('sizelabel').textContent + '"');

    press(document.getElementById('sizebtn'), 'the size dropdown open again');
    const hd = [...document.querySelectorAll('#sizemenu .opt')]
      .find((o) => o.dataset.id === '1920x1080');
    press(hd, 'minimum size 1920x1080');

    press(document.getElementById('colourbtn'), 'the colour dropdown open');
    const swatches = [...document.querySelectorAll('#colourmenu .opt')];
    note('selftest sees ' + swatches.length + ' colours');
    const black = swatches.find((o) => o.dataset.id === '#000000');
    press(black, 'colour Black');
    press(document.getElementById('colourbtn'), 'the colour dropdown open again');
    press(swatches.find((o) => o.dataset.id === ''), 'colour Any (should clear)');
    press(document.getElementById('colourbtn'), 'the colour dropdown once more');
    press(black, 'colour Black again');

    press(document.getElementById('searchbtn'), 'SEARCH');
    await until('the how-many stage', () => frame.dataset.stage === 'ask');

    /* Stage two. Prove the box holds 10 unprompted, then override it. */
    note('selftest sees how-many prefilled with "' + howmany.value + '"');
    const ask = document.getElementById('ask');
    if (ask.scrollHeight > ask.clientHeight + 1 || ask.scrollWidth > ask.clientWidth + 1) {
      fails_forget.push('how-many window clips its contents: ' +
                        ask.scrollWidth + 'x' + ask.scrollHeight + ' inside ' +
                        ask.clientWidth + 'x' + ask.clientHeight);
    }
    note('ASK content=' + ask.scrollWidth + 'x' + ask.scrollHeight +
         ' window=' + ask.clientWidth + 'x' + ask.clientHeight);
    howmany.value = '9';
    howmany.dispatchEvent(new Event('input'));
    press(document.getElementById('askgo'), 'CONFIRM 9');

    await until('the grid stage', () => frame.dataset.stage === 'grid');

    /* Roll the window up and back down while it fills. */
    await wait(500);
    press(document.getElementById('rollbtn'), 'ROLL UP');
    await wait(400);
    press(document.getElementById('rollbtn'), 'ROLL DOWN');

    /* Let a few land, then try to submit while it is still running -- which
     * must be refused. That refusal is the point of the button's rule. */
    /* Eight, not four: with two sources taking four each per turn, stopping
     * at four would only ever prove the first source works. */
    await until('eight previews', () => results.children.length >= 8);
    submitbtn.click();
    note('selftest tried SUBMIT mid-search; hidden=' + submitbtn.hidden);

    press(results.children[0], 'tick preview 1');
    press(results.children[2], 'tick preview 3');

    press(stopbtn, 'STOP');
    await until('stopped', () => !running);
    note('selftest sees button now reads "' + stopbtn.textContent + '"' +
         ', submit hidden=' + submitbtn.hidden);

    press(submitbtn, 'SUBMIT SELECTIONS');
    /* Which now asks where to put them rather than saving straight away. */
    if (document.getElementById('savepanel').hidden) {
      fails_forget.push('submit did not ask where to save');
    }
    note('selftest sees the save panel offering ' +
         document.getElementById('savedirlabel').textContent);
    /* The folder chooser itself is NOT pressed. It opens the system's own
     * modal dialog, which nothing in this page can close again -- a self-test
     * that opens it would hang there until someone came and clicked it. That
     * it is wired at all is proved by the line the host writes when it opens;
     * a person has to be the one to prove the rest. */
    press(document.getElementById('saveconfirm'), 'SAVE into the shown folder');
    /* Wait for the download rather than a fixed delay: a full-size wallpaper
     * is several megabytes and the window used to close out from under it. */
    await until('the save to finish', () => window.__lastSave, 900);
    note('selftest save result ' + JSON.stringify(window.__lastSave));

    press(stopbtn, 'RESUME');
    await until('running again', () => running);
    await wait(700);

    /* The X, and the question it has to ask. Answer no first -- a confirm box
     * that only works one way is half-tested. */
    press(document.getElementById('gridclose'), 'X');
    await until('the question', () => !confirm.hidden);
    press(document.getElementById('confirmno'), 'NO, keep them');
    await until('the question closed', () => confirm.hidden);

    press(document.getElementById('gridclose'), 'X again');
    await until('the question', () => !confirm.hidden);
    press(document.getElementById('confirmyes'), 'YES, delete them');
    await until('back at the bar', () => frame.dataset.stage === 'bar');

    /* Forgetting what has been seen. Only meaningful if there is a record to
       forget -- placeholder runs write none -- so it says which happened
       rather than passing quietly either way. */
    await wait(200);
    if (forgetbtn.disabled) {
      note('selftest FORGET SKIPPED: button reads "' + forgetbtn.textContent + '"');
    } else {
      note('selftest sees forget button reading "' + forgetbtn.textContent + '"');
      press(forgetbtn, 'FORGET WHAT I HAVE SEEN');
      await until('the question', () => !confirm.hidden);
      const panel = document.getElementById('confirmpanel').getBoundingClientRect();
      note('FORGET box=' + Math.round(panel.width) + 'x' + Math.round(panel.height) +
           ' asking: "' + confirmtext.textContent + '"');
      press(document.getElementById('confirmno'), 'NO, keep the record');
      await until('the question closed', () => confirm.hidden);
      if (forgetbtn.disabled) fails_forget.push('record cleared after answering No');
      press(forgetbtn, 'FORGET again');
      await until('the question', () => !confirm.hidden);
      press(document.getElementById('confirmyes'), 'YES, forget them');
      await until('the count to reset', () => forgetbtn.disabled);
      note('selftest forget left it reading "' + forgetbtn.textContent + '"');
    }
    if (fails_forget.length) note('FORGET FAILED: ' + fails_forget.join('; '));

    note('selftest complete');
  })();
}


/* ---------- the layout check ----------
 *
 * He found the grid stacking previews on top of each other past about twenty
 * results, and reported it because he could see it. This session cannot: it
 * has no screen and no pointer, so "do the cards overlap" has to become a
 * number.
 *
 * It measures real boxes after a real fill, and asserts three things a
 * working grid must satisfy. Run with WALLSCAN_SELFTEST=layout.
 */

if (SELFTEST === 'layout') {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const WANT = 48;
  const fails = [];
  const gapFromBottom = () =>
    results.scrollHeight - results.scrollTop - results.clientHeight;
  const atLeast = async (n, tries = 600) => {
    for (let i = 0; i < tries && results.children.length < n; i++) await wait(60);
  };

  (async () => {
    await wait(400);
    document.getElementById('searchbtn').click();
    await wait(400);
    howmany.value = String(WANT);
    howmany.dispatchEvent(new Event('input'));
    document.getElementById('askgo').click();

    /* --- following the feed, which he asked for so results can be watched
       going past rather than hunted for. Three states, each measured while
       previews are still arriving. --- */

    /* Wait until there is something to scroll. Below that, "following the
       feed" is not a behaviour: the content is shorter than the window, every
       preview is already visible and scrollTop can only be 0. An earlier
       version of this test asserted against a grid that had not overflowed
       yet and failed the code for it. */
    for (let i = 0; i < 900; i++) {
      if (results.scrollHeight > results.clientHeight + 200) break;
      await wait(60);
    }
    await wait(200);
    const g1 = gapFromBottom();
    if (g1 > 48) fails.push('not following the feed: ' + Math.round(g1) + 'px off the bottom');

    /* Scroll up, the way he would to click one going past. The feed must let
       go and leave him where he put himself. */
    results.scrollTop = 0;
    await wait(200);
    const before = results.children.length;
    await atLeast(before + 6);
    await wait(200);
    const heldAt = results.scrollTop;
    if (heldAt > 8) {
      fails.push('scrolled away from the top on its own, to ' +
                 Math.round(heldAt) + 'px');
    }

    /* Scroll back down to catch up. The feed must pick him up again. */
    results.scrollTop = results.scrollHeight;
    await wait(120);
    const here = results.children.length;
    await atLeast(here + 4);
    await wait(200);
    const g2 = gapFromBottom();
    if (g2 > 48) fails.push('did not resume following: ' + Math.round(g2) + 'px off the bottom');

    /* --- the grid itself, once it is full --- */

    await atLeast(WANT);
    await wait(500);
    const cards = [...results.children];
    const box = (el) => el.getBoundingClientRect();

    const flat = cards.filter((c) => box(c).height < 20);
    if (flat.length) fails.push(flat.length + ' of ' + cards.length + ' cards have no height');

    let overlaps = 0;
    for (let i = 0; i + 4 < cards.length; i++) {
      if (box(cards[i]).bottom > box(cards[i + 4]).top + 1) overlaps++;
    }
    if (overlaps) fails.push(overlaps + ' cards overlap the one below them');

    const firstTop = Math.round(box(cards[0]).top);
    const inRow = cards.filter((c) => Math.round(box(c).top) === firstTop).length;
    if (inRow !== 4) fails.push('first row holds ' + inRow + ' cards, expected 4');

    if (results.scrollHeight <= results.clientHeight) {
      fails.push('nothing to scroll: content ' + results.scrollHeight +
                 'px inside ' + results.clientHeight + 'px');
    }

    note('LAYOUT cards=' + cards.length +
         ' cardheight=' + Math.round(box(cards[0]).height) +
         ' firstrow=' + inRow +
         ' content=' + results.scrollHeight + 'px visible=' + results.clientHeight + 'px');
    note('FOLLOW pinned=' + Math.round(g1) + 'px from bottom, ' +
         'heldAtTop=' + Math.round(heldAt) + 'px, ' +
         'resumed=' + Math.round(g2) + 'px from bottom');
    note(fails.length ? 'LAYOUT FAILED: ' + fails.join('; ') : 'LAYOUT OK');
    note('selftest complete');
  })();
}


/* ---------- the soak ----------
 *
 * A long real search, left to finish, so duplicates have room to show up.
 * Both sources repeat themselves across pages -- he saw it in a hundred-result
 * run before anything guarded against it -- and a nine-result test is far too
 * short to catch that. Run with WALLSCAN_SELFTEST=soak.
 */

if (SELFTEST === 'soak') {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const WANT = 60;

  (async () => {
    await wait(400);
    document.getElementById('searchbtn').click();   /* no themes: widest pool */
    await wait(400);
    howmany.value = String(WANT);
    howmany.dispatchEvent(new Event('input'));
    document.getElementById('askgo').click();

    /* Wait for it to START before waiting for it to finish. `running` is
       set by the host a beat after the click, so testing it straight away
       reads the value from before the search and falls through at once --
       which is exactly what the first version of this did, reporting a pass
       on two results out of sixty. */
    for (let i = 0; i < 400 && !running; i++) await wait(50);
    for (let i = 0; i < 6000 && running; i++) await wait(100);
    await wait(800);

    const ids = [...results.children].map((c) => c.dataset.id);
    const seen = new Set();
    const twice = ids.filter((k) => seen.has(k) || (seen.add(k) && false));
    note('SOAK delivered=' + ids.length + ' distinct=' + seen.size +
         ' duplicates_on_screen=' + twice.length);
    note(twice.length ? 'SOAK FAILED: ' + [...new Set(twice)].join(', ')
                      : 'SOAK OK — no wallpaper appeared twice');
    note('selftest complete');
  })();
}
