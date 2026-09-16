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

const send = (channel, payload) =>
  window.webkit.messageHandlers[channel].postMessage(
    payload === undefined ? '' : payload);

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
let following = true;
const NEAR_BOTTOM = 48;

results.addEventListener('scroll', () => {
  const gap = results.scrollHeight - results.scrollTop - results.clientHeight;
  following = gap <= NEAR_BOTTOM;
});

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
window.scanner_setSources = (list) => {
  const box = document.getElementById('sourcelist');
  box.innerHTML = '';
  list.forEach((s) => {
    const b = document.createElement('button');
    b.className = 'pick' + (s.ready ? ' on' : '');
    b.dataset.id = s.id;
    b.textContent = s.name;
    if (!s.ready) {
      b.disabled = true;
      const why = document.createElement('span');
      why.className = 'why';
      why.textContent = s.why || 'not available';
      b.appendChild(why);
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

/* One colour at a time, not several. He described this as picking THE main
 * colour -- "if he wants something white, he picks white" -- and two main
 * colours is not a thing a picture has. Choosing one therefore clears the
 * rest rather than adding to them, and pressing the chosen one again turns it
 * off, so there is always a way back to no colour without hunting for "Any". */
window.scanner_setColours = (list) => {
  const box = document.getElementById('colourlist');
  box.innerHTML = '';

  const any = document.createElement('button');
  any.className = 'swatch any on';
  any.dataset.hex = '';
  any.textContent = 'Any colour';
  any.setAttribute('aria-label', 'Any colour');
  box.appendChild(any);

  const clear = () =>
    [...box.querySelectorAll('.swatch')].forEach((s) => s.classList.remove('on'));

  any.addEventListener('click', () => {
    clear();
    any.classList.add('on');
    note('colour cleared');
  });

  list.forEach((c) => {
    const b = document.createElement('button');
    b.className = 'swatch';
    b.dataset.hex = c.hex;
    b.style.background = c.hex;
    /* aria-label, never title: WebKit turns a title into a black GTK tooltip
     * that no stylesheet here can reach. */
    b.setAttribute('aria-label', c.name);
    b.addEventListener('click', () => {
      const was = b.classList.contains('on');
      clear();
      if (was) {
        any.classList.add('on');
        note('colour cleared');
      } else {
        b.classList.add('on');
        note('colour ' + c.name + ' (' + c.hex + ')');
      }
    });
    box.appendChild(b);
  });
};

const chosenColour = () => {
  const on = document.querySelector('#colourlist .swatch.on');
  return (on && on.dataset.hex) || '';
};

const chosen = (selector) =>
  [...document.querySelectorAll(selector + ' .pick.on')]
    .map((b) => b.dataset.id || b.textContent.trim());

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
  }));
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
  following = true;          /* a new search always starts by following */
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

  results.appendChild(card);

  /* Instant, not smooth: previews land in bursts, and queued smooth scrolls
   * fight each other and arrive late. Three appends in four move nothing
   * anyway -- the content only grows when a new row of four starts -- so this
   * reads as a steady creep rather than a jump. */
  if (following) results.scrollTop = results.scrollHeight;
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

submitbtn.addEventListener('click', () => {
  if (running) { note('submit refused: still searching'); return; }
  if (!picked.size) { note('submit refused: nothing selected'); return; }
  send('submit', JSON.stringify([...picked]));
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
    await until('sources', () => document.querySelectorAll('#sourcelist .pick').length);
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
    const sources = [...document.querySelectorAll('#sourcelist .pick:not([disabled])')];
    press(sources[1], 'source off (' + sources[1].textContent + ')');
    press(sources[1], 'source back on');
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

    const swatches = [...document.querySelectorAll('#colourlist .swatch')];
    note('selftest sees ' + swatches.length + ' colour swatches');
    /* Wrapping onto a clipped second row is the defect this checks for; it
     * happened once, at 30px swatches, and was fixed to 28. */
    const pane = document.getElementById('colourpane');
    if (pane.scrollHeight > pane.clientHeight + 1) {
      fails_forget.push('colour row is clipped: ' + pane.scrollHeight +
                        ' inside ' + pane.clientHeight);
    }
    const black = swatches.find((s) => s.dataset.hex === '#000000');
    press(black, 'colour Black');
    press(black, 'colour Black again (should clear)');
    press(black, 'colour Black once more');

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
