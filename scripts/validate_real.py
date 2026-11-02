"""Real API acceptance runner; never seeds, substitutes or mocks provider outputs.
First run processes an advertisement. Review assets in the UI before the second
run with --confirm-reviewed; only then does it export all four formats.
"""
import argparse
import json
import time
from pathlib import Path
import httpx

parser=argparse.ArgumentParser()
parser.add_argument('jpg',nargs='?',type=Path)
parser.add_argument('--server',default='http://127.0.0.1:8000')
parser.add_argument('--job')
parser.add_argument('--confirm-reviewed',action='store_true')
args=parser.parse_args()


def checked(response):
    if not response.is_success:
        raise SystemExit(f'HTTP {response.status_code}: {response.text[:500]}')
    return response.json()


with httpx.Client(base_url=args.server,timeout=300) as client:
    if args.job:
        job=checked(client.get('/api/jobs/'+args.job))
    else:
        if not args.jpg:parser.error('Provide a real JPG advertisement, or --job ID')
        with args.jpg.open('rb') as stream:
            job=checked(client.post('/api/jobs',files={'file':(args.jpg.name,stream,'image/jpeg')}))
        job=checked(client.post(f'/api/jobs/{job["id"]}/process'))
    print('JOB:',job['id'])
    deadline=time.monotonic()+1200
    while job['status']=='processing':
        if time.monotonic()>deadline:raise SystemExit('Timed out waiting; server task may still be processing. Resume with --job.')
        print(job['stage']);time.sleep(3)
        job=checked(client.get('/api/jobs/'+job['id']))
    if job['status']!='review':raise SystemExit(job.get('error') or job['status'])
    print(json.dumps(job.get('quality',{}),ensure_ascii=False,indent=2))
    if not args.confirm_reviewed:
        print('Review the source, reconstructed image and all intermediate assets in the UI.')
        print(f'After review: python scripts/validate_real.py --job {job["id"]} --confirm-reviewed')
        raise SystemExit(0)
    checked(client.post(f'/api/jobs/{job["id"]}/confirm'))
    results={}
    for key in checked(client.get('/api/config'))['sizes']:
        layout=checked(client.get(f'/api/jobs/{job["id"]}/layout/{key}'))
        output=checked(client.post(f'/api/jobs/{job["id"]}/export',json=layout))
        results[key]=output['validation']
    print(json.dumps({'job':job['id'],'exports':results},indent=2))
