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
    parser.add_argument('--probe',action='store_true',help='Send one tiny text-only vision-model request (may incur API charges)')
    args=parser.parse_args()
    cfg=settings()
    print(json.dumps({'api_key_present':bool(cfg['key']),'api_hostname':urlsplit(cfg['base']).hostname,
                      'vision_model':cfg['vision'],'image_model':cfg['image']},ensure_ascii=False,indent=2))
    if not args.probe:
        print('Local check only. Use --probe for one small real API request; this may incur a small charge.')
        return 0
    try:
        api=OpenAIImages()
        api.request('/chat/completions',json={'model':cfg['vision'],
            'messages':[{'role':'user','content':'Reply OK.'}],'max_tokens':8})
    except ProviderError as error:
        print(str(error))
        print(json.dumps(error.details,ensure_ascii=False,indent=2))
        return 1
    print('Vision-model API request succeeded. This does not validate image-model limits or image-edit quality.')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
