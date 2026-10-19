"""Bounded, portable PE version-resource reads; never loads or executes NX.

Format references: Microsoft PE .rsrc, VS_VERSIONINFO and String documentation.
Metadata identifies configured versions, not authenticity, licensing or runtime.
"""
import hashlib
import re
import struct

from .safety import DomainError,offline_path,read_bounded_file

MAX_IMAGE_BYTES=16*1024*1024
STRINGS=('CompanyName','ProductName','ProductVersion','FileVersion','OriginalFilename')


def _identity(file_version,product_version,strings):
    if (set(strings)!=set(STRINGS) or any(not isinstance(text,str) or not 1<=len(text)<=512
            or any(ord(char)<32 for char in text) for text in strings.values())
            or 'siemens' not in strings['CompanyName'].casefold()
            or strings['ProductName'] not in ('NX','Designcenter NX')
            or strings['OriginalFilename'].casefold() not in ('ugraf','ugraf.exe')
            or file_version[0]!=product_version[0] or not 1000<=product_version[0]<=9999):return False
    for name,version in (('FileVersion',file_version),('ProductVersion',product_version)):
        if not re.fullmatch(r'\d+(?:\.\d+){1,3}',strings[name]):return False
        parts=[int(part) for part in strings[name].split('.')]
        if parts+[0]*(4-len(parts))!=version:return False
    return True


def _bad():
    raise DomainError('NX installation has no supported, unambiguous PE version metadata.')


def _version_resource(raw):
    def take(offset,size):
        if offset<0 or size<0 or offset+size>len(raw):_bad()
        return raw[offset:offset+size]
    def numbers(fmt,offset):return struct.unpack(fmt,take(offset,struct.calcsize(fmt)))
    if take(0,2)!=b'MZ':_bad()
    pe=numbers('<I',0x3c)[0]
    if pe<64 or take(pe,4)!=b'PE\0\0':_bad()
    sections=numbers('<H',pe+6)[0];optional_size=numbers('<H',pe+20)[0];optional=pe+24
    if not 1<=sections<=96:_bad()
    take(optional,optional_size);magic=numbers('<H',optional)[0]
    directories={0x10b:96,0x20b:112}.get(magic)
    if directories is None or optional_size<directories+24 or numbers('<I',optional+directories-4)[0]<3:_bad()
    resource_rva,resource_size=numbers('<II',optional+directories+16)
    if not resource_rva or not 16<=resource_size<=MAX_IMAGE_BYTES:_bad()
    mappings=[]
    for index in range(sections):
        section=optional+optional_size+40*index;take(section,40)
        virtual_size,rva,size,offset=numbers('<IIII',section+8)
        if size:take(offset,size)
        mappings.append((rva,size,offset))
    def mapped(rva,size):
        matches=[offset+rva-start for start,length,offset in mappings if start<=rva and rva+size<=start+length]
        if len(matches)!=1:_bad()
        take(matches[0],size)
        return matches[0]
    base=mapped(resource_rva,resource_size)
    def resource(offset,size):
        if offset<0 or size<0 or offset+size>resource_size:_bad()
        return take(base+offset,size)
    def directory(offset):
        if offset%4:_bad()
        header=resource(offset,16);named,ids=struct.unpack_from('<HH',header,12);count=named+ids
        if not 1<=count<=4096:_bad()
        entries=list(struct.iter_unpack('<II',resource(offset+16,8*count)))
        if len({key for key,value in entries})!=count:_bad()
        return entries
    types=[value for key,value in directory(0) if key==16]
    if len(types)!=1 or not types[0]&0x80000000:_bad()
    seen={0};leaves=[]
    def descend(offset,level):
        if offset in seen:_bad()
        seen.add(offset)
        for key,value in directory(offset):
            if level==1:
                if not value&0x80000000:_bad()
                descend(value&0x7fffffff,2)
            else:
                if value&0x80000000 or len(leaves)>=32:_bad()
                rva,size,codepage,reserved=struct.unpack('<IIII',resource(value,16))
                if reserved or not 1<=size<=65536 or not resource_rva<=rva or rva+size>resource_rva+resource_size:_bad()
                leaves.append(take(mapped(rva,size),size))
    descend(types[0]&0x7fffffff,1)
    if not leaves:_bad()
    return leaves


def _metadata(raw):
    count=0
    def node(offset,limit,depth=0):
        nonlocal count
        count+=1
        if count>512 or depth>4 or offset%4 or offset+6>limit:_bad()
        length,value_length,kind=struct.unpack_from('<HHH',raw,offset);end=offset+length
        if length<8 or end>limit or kind not in (0,1):_bad()
        cursor=offset+6;start=cursor
        while cursor+2<=end and cursor-start<=256:
            if raw[cursor:cursor+2]==b'\0\0':break
            cursor+=2
        else:_bad()
        key=raw[start:cursor].decode('utf-16le');cursor=(cursor+2+3)&~3
        value_end=cursor+value_length*(2 if kind else 1)
        if value_end>end:_bad()
        value=raw[cursor:value_end];children=[];cursor=(value_end+3)&~3
        while cursor<end:
            if end-cursor<=3 and not any(raw[cursor:end]):break
            child,next_offset=node(cursor,end,depth+1);children.append(child);cursor=(next_offset+3)&~3
        return (key,kind,value,children),end
    root,end=node(0,len(raw))
    key,kind,fixed,children=root
    if key!='VS_VERSION_INFO' or kind!=0 or len(fixed)!=52 or len(raw)-end>3 or any(raw[end:]):_bad()
    fields=struct.unpack('<13I',fixed)
    if fields[0]!=0xFEEF04BD or fields[1]!=0x10000 or fields[9]!=1:_bad()
    versions=[[fields[index]>>16,fields[index]&65535,fields[index+1]>>16,fields[index+1]&65535] for index in (2,4)]
    strings={};sfi=[child for child in children if child[0]=='StringFileInfo']
    if len(sfi)!=1 or sfi[0][1]!=1 or sfi[0][2]:_bad()
    for table in sfi[0][3]:
        if not re.fullmatch('[0-9a-fA-F]{8}',table[0]) or table[1]!=1 or table[2]:_bad()
        seen=set()
        for name,kind,value,children in table[3]:
            if name in seen or kind!=1 or children:_bad()
            seen.add(name)
            if name not in STRINGS:continue
            if not value.endswith(b'\0\0'):_bad()
            text=value[:-2].decode('utf-16le')
            if not 1<=len(text)<=512 or any(ord(char)<32 for char in text):_bad()
            if name in strings and strings[name]!=text:_bad()
            strings[name]=text
    file_version,product_version=versions
    if not _identity(file_version,product_version,strings):_bad()
    return {'nx_version':str(product_version[0]),'file_version':file_version,'product_version':product_version,'strings':strings}


def installation_version(root):
    path=offline_path(root/'NXBIN/ugraf.exe')
    raw=read_bounded_file(path,max_bytes=MAX_IMAGE_BYTES,limit_message='NX executable metadata file exceeds the bounded reader.')
    try:
        versions=[_metadata(value) for value in _version_resource(raw)]
    except (UnicodeError,struct.error,OverflowError) as error:
        raise DomainError('NX installation contains malformed version metadata.') from error
    if any(value!=versions[0] for value in versions[1:]):_bad()
    return {'contract_version':1,'scope':'nx_installation_version_metadata','path':str(path),
        'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),**versions[0],
        'metadata_only':True,'digital_signature_verified':False,'nx_execution_verified':False}


def version_evidence(value,expected):
    """Stored contract check, without reading historic paths or executing files."""
    return (isinstance(value,dict) and type(value.get('contract_version')) is int and value['contract_version']==1
        and value.get('scope')=='nx_installation_version_metadata' and value.get('nx_version')==expected
        and value.get('metadata_only') is True and value.get('nx_execution_verified') is False
        and value.get('digital_signature_verified') is False
        and isinstance(value.get('path'),str) and bool(value['path'])
        and type(value.get('size')) is int and 1<=value['size']<=MAX_IMAGE_BYTES
        and isinstance(value.get('sha256'),str) and re.fullmatch('[a-f0-9]{64}',value['sha256']) is not None
        and all(isinstance(value.get(name),list) and len(value[name])==4
            and all(type(part) is int and 0<=part<=65535 for part in value[name])
            and str(value[name][0])==expected for name in ('file_version','product_version'))
        and isinstance(value.get('strings'),dict) and _identity(value['file_version'],value['product_version'],value['strings']))
