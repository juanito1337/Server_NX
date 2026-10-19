"""Own inert PE/version-resource fixtures. No executable code or vendor bytes."""
import struct


def align(value):
    return (value+3)&~3


def node(key,value=b'',children=(),kind=1):
    header=struct.pack('<HHH',0,len(value)//2 if kind else len(value),kind)+(key+'\0').encode('utf-16le')
    result=header.ljust(align(len(header)),b'\0')+value
    for child in children:
        result=result.ljust(align(len(result)),b'\0')+child
    return struct.pack('<H',len(result))+result[2:]


def synthetic_nx_image(version='2512',build=8500,*,pe32=False,strings=None,file_version=None,product_version=None,other_resources=()):
    """One resource section, zero entry point, no code/imports. Never execute it."""
    fv=file_version or (int(version),build,0,0);pv=product_version or fv
    fixed=struct.pack('<13I',0xFEEF04BD,0x10000,(fv[0]<<16)|fv[1],(fv[2]<<16)|fv[3],
        (pv[0]<<16)|pv[1],(pv[2]<<16)|pv[3],0x3f,0,0x40004,1,0,0,0)
    values={'CompanyName':'Siemens (synthetic test metadata)',
        'ProductName':'NX' if version=='2406' else 'Designcenter NX',
        'ProductVersion':str(pv[0])+'.'+str(pv[1]),'FileVersion':str(fv[0])+'.'+str(fv[1]),'OriginalFilename':'ugraf'}
    if strings is not None:values.update(strings)
    table=node('040904b0',children=[node(key,(value+'\0').encode('utf-16le')) for key,value in values.items()])
    resource=node('VS_VERSION_INFO',fixed,[node('StringFileInfo',children=[table])],kind=0)
    resources=[resource,*other_resources];count=len(resources);data_entries=64+8*count
    rsrc=bytearray(data_entries+16*count)
    for offset in (0,24):struct.pack_into('<HH',rsrc,offset+12,0,1)
    struct.pack_into('<HH',rsrc,60,0,count)
    struct.pack_into('<II',rsrc,16,16,0x80000018)
    struct.pack_into('<II',rsrc,40,1,0x80000030)
    for index,value in enumerate(resources):
        rsrc.extend(b'\0'*(align(len(rsrc))-len(rsrc)))
        struct.pack_into('<II',rsrc,64+8*index,1033+index,data_entries+16*index)
        struct.pack_into('<IIII',rsrc,data_entries+16*index,0x1000+len(rsrc),len(value),1200,0)
        rsrc+=value
    optional_size=224 if pe32 else 240;directories=96 if pe32 else 112
    image=bytearray(512)
    image[:2]=b'MZ';struct.pack_into('<I',image,0x3c,0x80);image[0x80:0x84]=b'PE\0\0'
    struct.pack_into('<HHIIIHH',image,0x84,0x14c if pe32 else 0x8664,1,0,0,0,optional_size,2)
    struct.pack_into('<H',image,0x98,0x10b if pe32 else 0x20b)
    struct.pack_into('<I',image,0x98+directories-4,16)
    struct.pack_into('<II',image,0x98+directories+16,0x1000,len(rsrc))
    section=0x98+optional_size
    image[section:section+8]=b'.rsrc\0\0\0'
    struct.pack_into('<IIII',image,section+8,len(rsrc),0x1000,len(rsrc),512)
    struct.pack_into('<I',image,section+36,0x40000040)
    return bytes(image+rsrc)


def write_nx_image(root,version='2512',**options):
    target=root/'NXBIN/ugraf.exe';target.parent.mkdir(parents=True,exist_ok=True)
    target.write_bytes(synthetic_nx_image(version,**options))
    return target


def windows_version_metadata(path):
    """Independent Windows-system version.dll read. Never loads the target image."""
    import ctypes
    import os
    from pathlib import Path
    from ctypes import wintypes
    from nxserver.safety import offline_path
    path=offline_path(path)
    dll=ctypes.WinDLL(str(offline_path(Path(os.environ['SystemRoot'])/'System32/version.dll')),use_last_error=True)
    dll.GetFileVersionInfoSizeW.argtypes=[wintypes.LPCWSTR,ctypes.POINTER(wintypes.DWORD)]
    dll.GetFileVersionInfoSizeW.restype=wintypes.DWORD
    dll.GetFileVersionInfoW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p]
    dll.GetFileVersionInfoW.restype=wintypes.BOOL
    dll.VerQueryValueW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR,ctypes.POINTER(ctypes.c_void_p),ctypes.POINTER(wintypes.UINT)]
    dll.VerQueryValueW.restype=wintypes.BOOL
    ignored=wintypes.DWORD();size=dll.GetFileVersionInfoSizeW(str(path),ctypes.byref(ignored))
    assert 0<size<=1024*1024
    buffer=ctypes.create_string_buffer(size)
    assert dll.GetFileVersionInfoW(str(path),0,size,buffer)
    def query(key):
        pointer=ctypes.c_void_p();length=wintypes.UINT()
        assert dll.VerQueryValueW(buffer,key,ctypes.byref(pointer),ctypes.byref(length))
        return pointer,length.value
    pointer,length=query('\\');assert length==52
    fixed=struct.unpack('<13I',ctypes.string_at(pointer,52));assert fixed[0]==0xFEEF04BD
    versions=[[fixed[index]>>16,fixed[index]&65535,fixed[index+1]>>16,fixed[index+1]&65535] for index in (2,4)]
    strings={}
    for name in ('CompanyName','ProductName','ProductVersion','FileVersion','OriginalFilename'):
        pointer,length=query('\\StringFileInfo\\040904b0\\'+name)
        assert 0<length<=513;strings[name]=ctypes.wstring_at(pointer,length).rstrip('\0')
    return {'file_version':versions[0],'product_version':versions[1],'strings':strings}
