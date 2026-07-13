<%doc>
widen_links: list of {label, href, key, level, exists}
</%doc>
% if widen_links is not UNDEFINED and widen_links:
<section class="widen-nav" aria-label="Widen geographic scope">
  <strong class="widen-label">Widen:</strong>
  <span class="widen-links">
% for i, w in enumerate(widen_links):
  % if i > 0:
    <span class="widen-sep">·</span>
  % endif
  <a href="${w['href']}" class="widen-link${' missing' if w.get('exists') == '0' else ''}" title="${w.get('key') or ''}">${w['label']}</a>
% endfor
  </span>
</section>
% endif
