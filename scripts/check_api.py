"""Safe local configuration report; --probe sends a tiny, potentially billable request.

Run from the same environment as the app. No uploads, image calls, keys, upstream
messages or raw HTTP response bodies are printed.
"""
import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT/'.env')
from app.provider import OpenAIImages, ProviderError, settings


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group()
    group.add_argument('--probe',action='store_true',help='Send a tiny vision-model text request (may incur charges)')
    group.add_argument('--layout-probe',action='store_true',help='Send a tiny JSON request to AD_LAYOUT_MODEL (may incur charges)')
    args=parser.parse_args()
    cfg=settings()
    print(json.dumps({'api_key_present':bool(cfg['key']),'api_hostname':urlsplit(cfg['base']).hostname,
                      'vision_model':cfg['vision'],'layout_model':cfg['layout'],'image_model':cfg['image']},ensure_ascii=False,indent=2))
    if not args.probe and not args.layout_probe:
        print('Local check only. Use --probe for one small real API request; this may incur a small charge.')
        return 0
    try:
        api=OpenAIImages()
        body={'model':cfg['layout'] if args.layout_probe else cfg['vision'],
              'messages':[{'role':'user','content':'Return JSON {"ok":true} only.' if args.layout_probe else 'Reply OK.'}]}
        if args.layout_probe:body['response_format']={'type':'json_object'}
        else:body['max_tokens']=8
        api.request('/chat/completions',json=body)
    except ProviderError as error:
        print(str(error))
        print(json.dumps(error.details,ensure_ascii=False,indent=2))
        return 1
    print('Vision-model API request succeeded. This does not validate image-model limits or image-edit quality.')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
