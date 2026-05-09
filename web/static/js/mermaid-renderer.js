/** Mermaid diagram rendering. */

let _init = false;

export async function renderMermaid(code) {
  if (!code) return '';
  if (!_init) {
    mermaid.initialize({ startOnLoad: false, theme: 'default' });
    _init = true;
  }
  try {
    const { svg } = await mermaid.render('mermaidSvg-' + Date.now(), code);
    return svg;
  } catch (e) {
    return null;
  }
}
