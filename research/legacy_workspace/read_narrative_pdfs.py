from pathlib import Path
from pypdf import PdfReader
import json
root=Path(__file__).resolve().parents[1]
out=root/'tmp'/'narrative_pdfs'
out.mkdir(parents=True,exist_ok=True)
for n,name in enumerate(['叙事经济学-邹佩轩.pdf','【华源证券】叙事经济学2（2026版）.pdf'],1):
    r=PdfReader(root/'docs'/name)
    pages=[{'page':i+1,'text':p.extract_text() or ''} for i,p in enumerate(r.pages)]
    (out/f'book{n}.json').write_text(json.dumps(pages,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/f'book{n}.txt').write_text('\n\n'.join(f'=== PDF页 {p["page"]} ===\n{p["text"]}' for p in pages),encoding='utf-8')
    print(json.dumps({'book':n,'name':name,'pages':len(pages),'chars':sum(len(p['text']) for p in pages),'empty':[p['page'] for p in pages if len(p['text'])<50]},ensure_ascii=False))
