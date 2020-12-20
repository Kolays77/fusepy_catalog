import errno
import stat
import time
import os
from fuse import FUSE, FuseOSError, Operations, LoggingMixIn

FILE_MODE = 0o100666
STD_DIRS = ["by Artist", "by Album", "by Title"]


class Property(dict):
    def __init__(self, st_mode=0o000000, st_nlink=0, st_size=0, st_ctime=0, st_mtime=0, st_atime=0, st_gid=0, st_uid=0,
                 st_blocks=0):
        super().__init__()
        self.st_mode = st_mode
        self.st_nlink = st_nlink
        self.st_size = st_size
        self.st_ctime = st_ctime
        self.st_mtime = st_mtime
        self.st_atime = st_atime
        self.st_gid = os.getgid()
        self.st_uid = os.getuid()
        self.st_blocks = st_blocks


class Directory(object):
    def __init__(self, files, directories, properties):
        self.files = files
        self.directories = directories
        self.properties = properties

    def get_dir(self, dirname: str):
        return self.directories[dirname]


class File(object):
    def __init__(self, data, properties, paths=None, flag=False):
        self.data = data
        self.properties = properties
        self.paths = paths
        self.flag = flag


    def set_data(self, data):
        self.data = data


class Catalog(LoggingMixIn, Operations):
    # Initialization:
    def __init__(self):
        self.is_empty = True  # has or not songs
        self.filesystem = {}  # map(dict) of Directory
        self.fd = 0
        now = time.time()

        self.filesystem['/'] = Directory(files={}, directories={}, properties=Property(
            st_mode=0o040555, st_nlink=2, st_size=0, st_ctime=now,
            st_mtime=now, st_atime=now, st_gid=os.getgid(), st_uid=os.getuid()))
        self.add_dir("ALL", self.filesystem['/'], 0o040666)


    def get_newname(self, filename):
        prefix = filename + "_"
        same_names = [
            name for name in self.filesystem['/'].directories["ALL"].files if prefix in name]
        i = 2
        while prefix + str(i) in same_names:
            i += 1
        return prefix + str(i)


    @staticmethod
    def statfs():
        # modify latter
        return dict(f_bsize=512, f_blocks=20000, f_bavail=10000)


    # File functions:
    def create(self, path, mode):  # actually add file to /ALL
        filename = self.get_filename(path)
        dirname = self.get_dirname(path)
        if dirname != "/ALL":
            raise FuseOSError(errno.EROFS)  # READ ONLY FS
        self.add_file(filename, bytearray(), self.get_dir(dirname), FILE_MODE)
        return self.fd


    def __write(self, path, data, offset):
        st = self.get_file(path)
        st.data[offset:] = data
        size = len(st.data)
        st.properties.st_size = size
        st.properties.st_blocks = size // 512
        self.filesystem['/'].directories["ALL"].properties.st_size += size
        self.filesystem['/'].properties.st_size += size


    # add to check right data
    def write(self, path, data, offset, fh):
        filename = self.get_filename(path)
        # it always non NONE,is is calling after create file
        st = self.get_file(path)
        if st.flag:
            filename = self.get_newname(filename)
            self.add_file(filename, bytearray(),
                          self.filesystem['/'].directories["ALL"], FILE_MODE)
        self.__write(path, data, offset)

        dirs = self.parse_data(data)  # list dirs [artist, album, title]
        self.add_file_to_dirs(filename, data, dirs)
        st.flag = False
        return len(data)


    def open(self, path, flags):
        self.fd += 1
        return self.fd


    def read(self, path, size, offset, fh):
        fileobj = self.get_file(path)
        return bytes(fileobj.data[offset:(offset + size)])


    def setxattr(self, path, name, value, options, position=0):
        st = self.get_file(path)
        if not st:
            st = self.get_dir(path)
        attrs = st.setdefault('attrs', {})
        attrs[name] = value


    def truncate(self, path, length, fh=None):
        filename = self.get_filename(path)
        st = self.get_file(path)
        if length == 0 and len(st.data) != 0:
            st.flag = True
        else:
            st.data = st.data[:length]
            st.properties.st_size = length
            st.properties.st_blocks = length // 512
        self.update(filename, st.data)


    def readlink(self, path):
        st = self.get_file(path)
        return st.data


    def destroy(self, path):
        print("i'll be back.")


    def unlink(self, path):
        filename = self.get_filename(path)
        path_dirs = self.filesystem['/'].get_dir("ALL").files[filename].paths
        for path in path_dirs:
            st = self.get_dir(path)
            st.files.pop(filename)
        self.update()


    # save protected function, we remove when ALL empty
    def __rmdir(self, dirname):
        self.filesystem['/'].directories["ALL"].properties.st_nlink -= 1
        self.filesystem['/'].directories.pop(dirname)


    def update(self, filename=None, data_=None):
        if len(self.filesystem['/'].get_dir("ALL").files) == 0 and len(self.filesystem['/'].directories) > 2:
            for dir in STD_DIRS:
                self.__rmdir(dir)
            self.is_empty = True

        if data_ is not None:
            paths = self.filesystem['/'].get_dir("ALL").files[filename].paths
            for pth in paths:
                file_obj = self.get_file(pth + "/" + filename)
                file_obj.data = data_
    

    # Common functions
    def getattr(self, path, fh=None):
        st = self.get_file(path)
        if not st:
            st = self.get_dir(path)
        if not st:
            raise FuseOSError(errno.ENOENT)
        return st.properties.__dict__


    def getxattr(self, path, name, position=0):
        st = self.get_file(path)
        if not st:
            st = self.get_dir(path)
        attrs = st.properties.get('attrs', {})
        try:
            return attrs[name]
        except KeyError:
            return b''


    def listxattr(self, path):
        st = self.get_file(path)
        if not st:
            st = self.get_dir(path)
        attrs = st.properties.get('attrs', {})
        return list(attrs.keys())


    # modify update
    def rename(self, old, new):
        oldname = self.get_filename(old)
        newname = self.get_filename(new)
        paths = self.filesystem['/'].get_dir("ALL").files[oldname].paths

        for pth in paths:
            dirobj = self.get_dir(pth)
            dirobj.files[newname] = dirobj.files.pop(oldname)


    def removexattr(self, path, name):
        st = self.get_file(path)
        if not st:
            st = self.get_dir(path)
        attrs = st.properties.get('attrs', {})
        try:
            del attrs[name]
        except KeyError:
            pass

    # Directory functions:
    def readdir(self, path, fh):
        st = self.get_dir(path)
        return ['.', '..'] + [x for x in st.files] + [x for x in st.directories]

    # Helpers:
    def add_dir(self, dirname, parent_obj: Directory, mode):
        now = time.time()
        parent_obj.directories[dirname] = Directory(files={}, directories={}, properties=Property(
            st_mode=mode, st_nlink=2, st_size=0, st_ctime=now,
            st_mtime=now, st_atime=now, st_gid=os.getgid(), st_uid=os.getuid()))
        parent_obj.properties.st_nlink += 1


    def add_file(self, filename, data_, dir_obj, mode):
        size = 0
        if data_ != bytearray():
            size = len(data_)
        now = time.time()
        dir_obj.files[filename] = File(data=data_, properties=Property(
            st_mode=mode, st_nlink=1, st_size=size, st_ctime=now, st_mtime=now, st_atime=now))
        self.fd += 1

    
    def add_file_to_dirs(self, filename, data, dirs):
        # list dirs [artist, album, title]
        default_dirs = STD_DIRS
        if self.is_empty:
            for dir_name in default_dirs:
                self.add_dir(dir_name, self.filesystem['/'], 0o040666)
        self.is_empty = False
        paths = [] 
        for dir, def_dir in zip(dirs, default_dirs):
            letter = dir[0]
            paths.append("/" + def_dir + "/" + letter + "/" + dir)
            def_dirobj = self.filesystem['/'].get_dir(def_dir)
            if def_dirobj.directories.get(letter) is None:
                self.add_dir(letter, def_dirobj, 0o040555)

            dir_letter_obj = def_dirobj.directories[letter]
            if dir_letter_obj.directories.get(dir) is None:
                self.add_dir(dir, dir_letter_obj, 0o040555)

            cur_dir_obj = dir_letter_obj.get_dir(dir)
            self.add_file(filename, data, cur_dir_obj, FILE_MODE)

        paths.append("/ALL")
        self.filesystem['/'].get_dir("ALL").files[filename].paths = paths


    def cut_prefixes(self, target, prefixes):  # cut : THE the THe
        for prefix in prefixes:
            if prefix in target:
                return target[len(prefix):]
        return target

    #parse album, title, artist
    def parse_data(self, data):
        prefixes = ["THE", "the", "The"]
        lines = (data.decode('ascii')).split('\n')
        dirs = []
        for i in range(3):
            dirs.append(self.cut_prefixes(lines[i].split(':')[1].strip(),
                                          prefixes).strip())
        return dirs


    def get_filename(self, path):
        return path.split('/')[-1]


    def get_dirname(self, path):
        return '/'.join(path.split('/')[:-1])


    #get file object from path
    def get_file(self, path):
        if path[-1] == '/':
            return None
        else:
            patharray = path.split('/')
            filename = patharray.pop()
            dirname = '/'.join(path.split('/')[:-1])
            location = self.get_dir(dirname)
            if location and filename in location.files:
                return location.files[filename]
            return None


    #get directory object from path
    def get_dir(self, path):
        path = path.rstrip('/')
        patharray = path.split('/')
        if len(patharray) <= 1:
            return self.filesystem['/']
        patharray.pop(0)
        location = self.filesystem['/']
        while patharray:
            dirpath = patharray.pop(0)
            if dirpath in location.directories:
                location = location.directories[dirpath]
            else:
                return None
        return location


    def __unicode__(self):
        return str(self)


"""
Usage: python3 catalog.py mountpoint -f (foregraund) -d (debug)
"""

if __name__ == '__main__':
    import argparse
    parser = parser = argparse.ArgumentParser(description="Filesystem Song Catalog")
    parser.add_argument('mountpoint', type=str, help='directory like songs/')
    args = parser.parse_args()
    fuse = FUSE(Catalog(), args.mountpoint, foreground=True, debug=True)
