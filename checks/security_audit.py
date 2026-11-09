"""Inspect tracked files and reachable Git blobs without printing secret values."""
import argparse
from collections import Counter
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess

from audit_release import PATTERNS, BAD_SUFFIXES

ROOT=Path(__file__).resolve().parents[1]
PRIVATE_DIRS={'work','data','backups','props','.ssh','.aws','.venv','venv','node_modules','__pycache__'}
PRIVATE_NAMES={'id_rsa','id_ed25519','id_ecdsa','id_dsa','.netrc','_netrc','.npmrc','.pypirc'}


def inspect(path,body):
    findings=[]
    p=PurePosixPath(path)
    if PRIVATE_DIRS.intersection(p.parts) or p.name in PRIVATE_NAMES or p.suffix.lower() in BAD_SUFFIXES or (p.name.startswith('.env') and p.name!='.env.example'):
        findings.append(dict(path=path,rule='private_or_runtime_path'))
    try:
        text=body.decode('utf-8-sig')
    except UnicodeError:
        return findings+[dict(path=path,rule='binary_requires_review')]
    for rule,pattern in PATTERNS.items():
        for match in re.finditer(pattern,text):
            value=match.group()
            if rule=='connection_password' and (any(mark in value for mark in (chr(34),chr(39))) or '${' in value):
                continue
            if rule=='email_address' and value.rsplit('@',1)[1].lower() in {'example.com','example.org','example.net','example.invalid'}:
                continue
            findings.append(dict(path=path,line=text.count('\n',0,match.start())+1,rule=rule))
    return findings


def run(git):
    def command(*args):
        return subprocess.check_output([git,*args],cwd=ROOT,stderr=subprocess.PIPE)
    commits=command('rev-list','--all').decode().splitlines()
    seen=set()
    findings=[]
    for commit in commits:
        entries=command('ls-tree','-r','-z',commit).split(b'\0')
        for entry in entries:
            if not entry:
                continue
            info,path=entry.split(b'\t',1)
            mode,kind,sha=info.decode().split()
            filename=path.decode('utf-8',errors='replace')
            if kind!='blob':
                findings.append(dict(path=filename,commit=commit[:12],rule='non_blob_requires_review'))
                continue
            if (sha,filename) in seen:
                continue
            seen.add((sha,filename))
            body=command('cat-file','blob',sha)
            for finding in inspect(filename,body):
                findings.append(dict(commit=commit[:12],**finding))
    tracked=command('ls-files','-z').split(b'\0')
    current=[]
    for entry in tracked:
        if entry:
            name=entry.decode('utf-8')
            current.extend(inspect(name,(ROOT/name).read_bytes()))
    # Include new source files without visiting ignored data/dependency trees.
    for entry in command('ls-files','--others','--exclude-standard','-z').split(b'\0'):
        if entry:
            name=entry.decode('utf-8')
            current.extend(inspect(name,(ROOT/name).read_bytes()))
    identity=Counter()
    new_identity=Counter()
    task_commits=set(command('rev-list','d04852c..HEAD').decode().splitlines())
    parts=command('log','--all','--format=%H%x00%ae%x00%ce%x00').decode().split('\0')
    for index in range(0,len(parts)-2,3):
        for address in parts[index+1:index+3]:
            domain=address.strip().lower().rsplit('@',1)[-1]
            category='github_noreply' if domain.endswith('noreply.github.com') else ('automation_domain' if domain in {'openai.com','localhost'} else 'other_contact_address')
            identity[category]+=1
            if parts[index].strip() in task_commits:
                new_identity[category]+=1
    ignored={}
    for name in ('.env','work/private.json','data/private.db','backups/private.dump','id_rsa'):
        ignored[name]=subprocess.run([git,'check-ignore','-q','--no-index',name],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0
    remote=command('remote','get-url','origin').decode()
    remote_with_credentials=bool(re.search(r'https?://[^/\s]+@',remote))
    result=dict(commits_scanned=len(commits),unique_blob_paths_scanned=len(seen),
        historical_findings=findings,current_findings=current,
        author_metadata_categories=dict(identity),new_task_author_metadata_categories=dict(new_identity),git_ignored_checks=ignored,
        git_remote_contains_credentials=remote_with_credentials,
        passed=not findings and not current and all(ignored.values()) and not remote_with_credentials,
        scope='Local reachable Git refs after fetch; no production environment, database or logs were accessed')
    (ROOT/'work').mkdir(exist_ok=True)
    (ROOT/'work/security-audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['passed'] else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--git',default=shutil.which('git'))
    args=parser.parse_args()
    if not args.git:
        parser.error('Provide --git executable path')
    raise SystemExit(run(args.git))
