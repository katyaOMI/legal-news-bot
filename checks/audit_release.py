"""Static publication audit. Findings contain paths/rule names, never secret values."""
import argparse
import ast
import json
import os
from pathlib import Path
import re

SKIP_DIRS={'.git','work','data','props','backups','node_modules','.venv','venv','__pycache__','.pytest_cache','.ruff_cache'}
BAD_SUFFIXES={'.pem','.key','.p12','.pfx','.jks','.sqlite','.sqlite3','.db','.dump','.log'}
PATTERNS={
 'telegram_token':r'\b\d{6,15}:[A-Za-z0-9_-]{30,}\b',
 'github_token':r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{35,})\b',
 'model_api_key':r'\b(?:sk-(?:proj-|or-v1-)?[A-Za-z0-9_-]{24,}|AIza[A-Za-z0-9_-]{30,}|hf_[A-Za-z0-9]{24,})\b',
 'aws_access_key':r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b',
 'private_key':r'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----',
 'connection_password':r'\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^\s:/@]+:[^\s/@]+@',
 'personal_windows_path':r'(?i)[a-z]:[\\/]+Users[\\/]+[^\\/\s]+',
 'email_address':r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b',
}


def audit(root,strict=False):
    findings=[]
    count=0
    for directory,dirs,files in os.walk(root,followlinks=False):
        for name in list(dirs):
            path=Path(directory)/name
            relative=path.relative_to(root).as_posix()
            if path.is_symlink() or name in SKIP_DIRS:
                if strict: findings.append(dict(path=relative,rule='excluded_directory_or_symlink'))
                dirs.remove(name)
        for name in files:
            path=Path(directory)/name
            relative=path.relative_to(root).as_posix()
            lower=name.lower()
            if path.is_symlink():
                findings.append(dict(path=relative,rule='symlink'))
                continue
            if (lower.startswith('.env') and lower!='.env.example') or path.suffix.lower() in BAD_SUFFIXES or lower in {'id_rsa','id_ed25519'} or re.match(r'(?i)(?:credentials|secrets).*\.json$',name):
                findings.append(dict(path=relative,rule='private_or_runtime_file'))
                continue
            try: text=path.read_text(encoding='utf-8-sig')
            except UnicodeError:
                findings.append(dict(path=relative,rule='unexpected_binary_file'))
                continue
            count+=1
            for rule,pattern in PATTERNS.items():
                for match in re.finditer(pattern,text):
                    value=match.group()
                    # Environment-variable references are templates, not credentials.
                    if rule=='connection_password' and any(mark in value for mark in (chr(34),chr(39))):
                        continue
                    if rule=='connection_password' and re.fullmatch(r'(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://\$\{[A-Z_]+\}:\$\{[A-Z_]+\}@',value):
                        continue
                    # Reserved documentation domains cannot identify a real person.
                    if rule=='email_address' and value.rsplit('@',1)[1].lower() in {'example.com','example.org','example.net','example.invalid'}:
                        continue
                    findings.append(dict(path=relative,line=text.count('\n',0,match.start())+1,rule=rule))
            if lower=='.env.example':
                for lineno,line in enumerate(text.splitlines(),1):
                    line=line.strip()
                    if line and not line.startswith('#') and '=' in line and line.split('=',1)[1].strip():
                        findings.append(dict(path=relative,line=lineno,rule='nonempty_environment_example'))
            if path.suffix=='.py':
                tree=ast.parse(text,filename=relative)
                for node in ast.walk(tree):
                    if isinstance(node,(ast.Assign,ast.AnnAssign)) and isinstance(node.value,ast.Constant) and isinstance(node.value.value,str) and node.value.value:
                        targets=node.targets if isinstance(node,ast.Assign) else [node.target]
                        if any(isinstance(t,ast.Name) and re.fullmatch(r'(?i)(?:telegram_token|api_key|password|secret|access_token)',t.id) for t in targets):
                            findings.append(dict(path=relative,line=node.lineno,rule='literal_secret_assignment'))
    return dict(files_scanned=count,findings=findings,passed=not findings)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder',type=Path)
    parser.add_argument('--release',action='store_true',help='Also reject runtime folders and Git history')
    args=parser.parse_args()
    root=args.folder.resolve()
    if not root.is_dir(): parser.error('Folder not found')
    result=audit(root,args.release)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['passed'] else 1


if __name__=='__main__': raise SystemExit(main())
