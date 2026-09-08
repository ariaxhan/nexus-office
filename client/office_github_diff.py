"""Immutable local Git fallback when GitHub refuses a large text diff."""
import os
import re
import subprocess

import office_objects as objects
import office_workspaces


def git(path,*args):
    return subprocess.run(['git','-C',str(path),'-c','core.hooksPath=/dev/null',*args],env=dict(os.environ,GIT_OPTIONAL_LOCKS='0',GIT_NO_REPLACE_OBJECTS='1'),capture_output=True,text=True,errors='replace',timeout=60)


def read(repo,base,head):
    if not all(isinstance(value,str) and re.fullmatch(r'[0-9a-f]{40}',value) for value in (base,head)):
        raise ValueError('Large diff requires exact base and head commits')
    for name,path in office_workspaces.discover(objects.vault()):
        if name!=repo:continue
        if git(path,'cat-file','-e',base+'^{commit}').returncode:continue
        if git(path,'cat-file','-e',head+'^{commit}').returncode:continue
        result=git(path,'diff','--no-ext-diff','--no-textconv',base+'...'+head,'--')
        if result.returncode:raise ValueError('Immutable local diff could not be read: '+result.stderr[:200])
        return result.stdout
    raise ValueError('GitHub cannot render this large diff; its exact base/head commits are not retained in a local checkout')
