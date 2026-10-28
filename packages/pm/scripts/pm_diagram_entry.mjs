import mermaid from 'mermaid';
import DOMPurify from 'dompurify';

mermaid.initialize({
  startOnLoad: false,
  securityLevel: 'strict',
  htmlLabels: false,
  maxTextSize: 10000,
  suppressErrorRendering: true,
  flowchart: { htmlLabels: false, useMaxWidth: true },
  er: { useMaxWidth: true },
});

export async function renderSafeDiagram(id, source) {
  const model = await analyzeSafeDiagram(source);
  const result = await mermaid.render(id, source);
  const svg = DOMPurify.sanitize(result.svg, {
    USE_PROFILES: { svg: true, svgFilters: false },
    FORBID_TAGS: ['script', 'foreignObject', 'iframe', 'image', 'a'],
    FORBID_ATTR: ['href', 'xlink:href', 'onclick', 'onload', 'style'],
  });
  return { svg, ...model };
}

export async function analyzeSafeDiagram(source) {
  if (typeof source !== 'string' || source.length > 10000) throw new Error('Diagram exceeds text limit');
  if (!/^\s*(?:flowchart(?:\s|$)|graph(?:\s|$)|erDiagram(?:\s|$))/.test(source)) throw new Error('Only flowchart and ER diagrams are supported');
  const diagram = await mermaid.mermaidAPI.getDiagramFromText(source);
  let nodes;
  let edges;
  if (diagram.type === 'flowchart-v2' || diagram.type === 'flowchart') {
    nodes = diagram.db.vertices instanceof Map ? diagram.db.vertices.size : 0;
    edges = Array.isArray(diagram.db.edges) ? diagram.db.edges.length : 0;
  } else if (diagram.type === 'er') {
    nodes = diagram.db.entities instanceof Map ? diagram.db.entities.size : 0;
    edges = Array.isArray(diagram.db.relationships) ? diagram.db.relationships.length : 0;
  } else {
    throw new Error('Only flowchart and ER diagrams are supported');
  }
  if (nodes > 100 || edges > 200) throw new Error(`Diagram exceeds safety limit (${nodes} nodes, ${edges} edges)`);
  return { nodes, edges };
}
