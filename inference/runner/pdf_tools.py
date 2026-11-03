#!/usr/bin/env python3
"""Render/crop original PDF pages and extract source image objects, with provenance."""
import argparse
import hashlib
import io
import json
import math
from pathlib import Path
import fitz
from PIL import Image


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def pages(spec,count):
    result=[]
    for part in (spec or '1').split(','):
        bounds=part.strip().split('-')
        if len(bounds) not in [1,2]:raise ValueError('Use pages such as 1-3,5')
        a,b=int(bounds[0]),int(bounds[-1])
        if not 1<=a<=b<=count:raise ValueError('PDF page number out of range')
        result.extend(range(a-1,b))
    return list(dict.fromkeys(result))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pdf',type=Path)
    parser.add_argument('--pages',default='1')
    parser.add_argument('--crop',help='x0,y0,x1,y1 in PDF points')
    parser.add_argument('--dpi',type=float,default=140)
    parser.add_argument('--max-side',type=int,default=1800)
    parser.add_argument('--max-pages',type=int,default=4)
    parser.add_argument('--out-dir',type=Path,required=True)
    parser.add_argument('--source-root',type=Path,help='When provided, only source PDFs below this directory are allowed')
    parser.add_argument('--extract-images',action='store_true')
    parser.add_argument('--list',action='store_true',help='List page sizes, image boxes and text without rendering')
    a=parser.parse_args();source=a.pdf.resolve(strict=True)
    if a.source_root and not source.is_relative_to(a.source_root.resolve()):
        raise ValueError('Visual PDF reads must use an original source file under materials/')
    if not math.isfinite(a.dpi) or not 36<=a.dpi<=240:raise ValueError('Use DPI in [36,240]')
    if not 256<=a.max_side<=2400:raise ValueError('Use max-side in [256,2400]')
    if not 1<=a.max_pages<=8:raise ValueError('Use max-pages in [1,8]')
    a.out_dir.mkdir(parents=True,exist_ok=True)
    source_sha=sha(source);output=[]
    with fitz.open(source) as doc:
        selected=pages(a.pages,len(doc))
        if len(selected)>a.max_pages:raise ValueError('Read a smaller page selection; maximum '+str(a.max_pages))
        region=None
        if a.crop:
            values=[float(x) for x in a.crop.split(',')]
            if len(values)!=4 or not all(math.isfinite(x) for x in values):raise ValueError('Crop needs 4 finite PDF-point coordinates')
            region=fitz.Rect(values)
            if region.is_empty or region.is_infinite:raise ValueError('Invalid crop rectangle')
        for index in selected:
            page=doc[index]
            if a.list:
                output.append({'page':index+1,'width_points':page.rect.width,'height_points':page.rect.height,
                    'text':page.get_text()[:5000],'image_boxes':[{'xref':v.get('xref'),'bbox':v['bbox'],'width':v['width'],'height':v['height']} for v in page.get_image_info(xrefs=True)]})
                continue
            if a.extract_images:
                seen=set()
                for item in page.get_images(full=True):
                    xref=item[0]
                    if xref in seen:continue
                    seen.add(xref)
                    pix=fitz.Pixmap(doc,xref)
                    if pix.width<180 or pix.height<120:continue
                    if item[1]:pix=fitz.Pixmap(pix,fitz.Pixmap(doc,item[1]))
                    if pix.n-pix.alpha>3:pix=fitz.Pixmap(fitz.csRGB,pix)
                    image=Image.open(io.BytesIO(pix.tobytes('png'))).convert('RGB')
                    image.thumbnail((a.max_side,a.max_side))
                    path=a.out_dir/f'{source_sha[:12]}-page-{index+1:03d}-xref-{xref}.png'
                    image.save(path,optimize=True)
                    output.append({'path':str(path.resolve()),'page':index+1,'xref':xref,'kind':'embedded_pdf_image',
                        'sha256':sha(path),'width':image.width,'height':image.height,'source_pdf_sha256':source_sha})
                    if len(output)>=12:break
                if len(output)>=12:break
                continue
            clip=page.rect if region is None else page.rect & region
            if clip.is_empty or clip.width<4 or clip.height<4:raise ValueError('Crop falls outside the page')
            scale=min(a.dpi/72,a.max_side/max(clip.width,clip.height))
            tag=hashlib.sha256(json.dumps([list(clip),scale]).encode()).hexdigest()[:10]
            path=a.out_dir/f'{source_sha[:12]}-page-{index+1:03d}-{tag}.png'
            pix=page.get_pixmap(matrix=fitz.Matrix(scale,scale),clip=clip,alpha=False)
            pix.save(path)
            output.append({'path':str(path.resolve()),'page':index+1,'crop_points':list(clip),'dpi':round(scale*72,3),
                'kind':'source_pdf_crop' if region is not None else 'source_pdf_page','sha256':sha(path),
                'width':pix.width,'height':pix.height,'source_pdf_sha256':source_sha})
        report={'source':str(source),'source_pdf_sha256':source_sha,'total_pages':len(doc),'images':output if not a.list else [],'pages':output if a.list else []}
    (a.out_dir/'last-operation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
