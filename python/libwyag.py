import argparse
import collections
import configparser
import hashlib
from math import ceil
import os
import re
import sys
import zlib


#####################################################################
# Classes
#####################################################################

class GitRepository(object):
    """A git repository"""

    worktree = None
    gitdir = None
    conf = None

    def __init__(self, path, force=False):
        self.worktree = path
        self.gitdir = os.path.join(path, ".git")

        if not (force or os.path.isdir(self.gitdir)):
            raise Exception("Not a Git repository %s" % path)

        # read in config file (.git/config)
        self.conf = configparser.ConfigParser()
        cf = repo_file(self, "config")

        if cf and os.path.exists(cf):
            self.conf.read([cf])
        elif not force:
            raise Exception("Configuration file missing")

        if not force:
            vers = int(self.conf.get("core", "repositoryformatversion"))
            if vers != 0:
                raise Exception("Unsupported repositoryformatversion %s" % vers)


# GitObject is abstract
class GitObject(object):
    """A generic representation of a git object."""
    repo = None

    def __init__(self, repo, data=None):
        self.repo = repo
        if data != None:
            self.deserialize(data)

    def serialize(self):
        raise Exception("Unimplemented!")

    def deserialize(self, data):
        raise Exception("Unimplemented!")


class GitBlob(GitObject):
    fmt = b'blob'

    def serialize(self):
        return self.blobdata

    def deserialize(self, data):
        self.blobdata = data


#####################################################################
# repo utilities
#####################################################################

def repo_path(repo, *path):
    """Compute path under repo's gitdir"""
    return os.path.join(repo.gitdir, *path)


def repo_file(repo, *path, mkdir=False):
    """Same as repo_path, but create dirname(*path) if absent. For
    example, repo_file(r, \"refs\", \"remotes\", \"origin\", \"HEAD\") will create
    .git/refs/remotes/origin."""
    if repo_dir(repo, *path[:-1], mkdir=mkdir):
        return repo_path(repo, *path)


def repo_dir(repo, *path, mkdir=False):
    """Same as repo_path, but mkdir *path if absent and mkdir is True"""
    path = repo_path(repo, *path)
    if os.path.exists(path):
        if (os.path.isdir(path)):
            return path
        else:
            raise Exception("Not a directory %s" % path)
    if mkdir:
        os.makedirs(path)
        return path
    else:
        return None


def repo_create(path):
    """Create a new repository at path."""
    repo = GitRepository(path, True)

    # First, make sure the path either doesn't exist or is an empty
    # directory.
    if os.path.exists(repo.worktree):
        if not os.path.isdir(repo.worktree):
            raise Exception("%s is not a directory!" % path)
        if os.listdir(repo.worktree):
            raise Exception("%s/.git is not empty - this is already a repo!" % path)
    else:
        os.makedirs(repo.worktree)

    # create directories in the .git/ dir called branches, objects,
    # refs/tags, and refs/heads
    assert (repo_dir(repo, "branches", mkdir=True))
    assert (repo_dir(repo, "objects", mkdir=True))
    assert (repo_dir(repo, "refs", "tags", mkdir=True))
    assert (repo_dir(repo, "refs", "heads", mkdir=True))

    # .git/description
    with open(repo_file(repo, "description"), "w") as f:
        f.write("Unnamed repository; edit this file 'description' to name the repository.\n")

    # .git/HEAD
    with open(repo_file(repo, "HEAD"), "w") as f:
        f.write("ref: refs/heads/main\n")

    # .git/config
    with open(repo_file(repo, "config"), "w") as f:
        config = repo_default_config()
        config.write(f)

    return repo


def repo_default_config():
    ret = configparser.ConfigParser()
    ret.add_section("core")
    ret.set("core", "repositoryformatversion", "0")
    ret.set("core", "filemode", "false")
    ret.set("core", "bare", "false")
    return ret


def repo_find(path=".", required=True):
    path = os.path.realpath(path)

    if os.path.isdir(os.path.join(path, ".git")):
        return GitRepository(path)

    parent = os.path.realpath(os.path.join(path, ".."))
    if parent == path:
        # os.path.join("/", "..") == "/"
        # we have hit the root, and not found a .git directory.
        if required:
            raise Exception("No git directory.")
        else:
            return None

    # otherwise, keep looking recursively...
    return repo_find(parent, required)


#####################################################################
# object utilities
#####################################################################
def object_read(repo, sha) -> GitObject:
    """Read object based on its SHA1 hash from git repository repo"""

    path = repo_file(repo, "objects", sha[0:2], sha[2:])
    with open(path, "rb") as f:
        # an object is stored on disk as:
        # <type in ASCII: tree, commit, blob, tag> <ASCII space (0x20)>
        # <size in ASCII> <null byte (0x00)>
        # <object contents - type dependent>
        raw = zlib.decompress(f.read())

        # read object type
        x = raw.find(b' ')
        fmt = raw[0:x]

        # read and validate object size
        y = raw.find(b'\x00', x)
        size = int(raw[x:y].decode("ascii"))
        if size != len(raw) - y - 1:
            raise Exception("Malformed object {0}: bad length".format(sha))

        # Based on the object type, pick a constructor
        if fmt == b'commit':
            c = GitCommit
        elif fmt == b'tree':
            c = GitTree
        elif fmt == b'tag':
            c = GitTag
        elif fmt == b'blob':
            c = GitBlob
        else:
            raise Exception("Unknown type \"{0}\" for object {1}".format(fmt.decode('ascii'), sha))

        return c(repo, raw[y + 1:])


def object_find(repo, name, fmt=None, follow=True):
    return name


def object_write(obj, actually_write=True):
    data = obj.serialize()

    # prepend header to data
    result = obj.fmt + b' ' + str(len(data)).encode() + b'\x00' + data

    # THEN calculate the hash
    # (this means the name of the file is dependent on the entire contents of the file!)
    sha = hashlib.sha1(result).hexdigest()

    if actually_write:
        # compute path
        path = repo_file(obj.repo, "objects", sha[0:2], sha[2:], mkdir=actually_write)

        with open(path, 'wb') as f:
            f.write(zlib.compress(result))

    return sha


#####################################################################
# main libwyag routine and cmd_* helpers
#####################################################################

# argparser for command line arguments
argparser = argparse.ArgumentParser(description="The stupidest content tracker")

# Allow (and require) subcommands (eg "add" "commit" "init" etc)
argsubparsers = argparser.add_subparsers(title="Commands", dest="command")
argsubparsers.required = True

# subparser for init
argsp = argsubparsers.add_parser("init", help="Initialize a new, empty repository.")
argsp.add_argument("path",
                   metavar="directory",
                   nargs="?",
                   default=".",
                   help="Where to create the repository.")

# subparser for cat-file
argsp = argsubparsers.add_parser("cat-file", help="Provide content of repository objects")
argsp.add_argument("type",
                   metavar="type",
                   choices=["blob", "commit", "tag", "tree"],
                   help="Specify the type")

argsp.add_argument("object",
                   metavar="object",
                   help="The object to display")

# argsubparser for hash-object
argsp = argsubparsers.add_parser(
    'hash-object',
    help='Compute object ID and optionally creates a blob from a file')

argsp.add_argument('-t',
                   metavar='type',
                   dest='type',
                   choices=['blob', 'commit', 'tag', 'tree'],
                   default='blob',
                   help='Specify the type')

argsp.add_argument('-w',
                   dest='write',
                   action='store_true',
                   help='Actually write the object into the database')

argsp.add_argument('path',
                   help='Read object from <file>')

def main(argv=sys.argv[1:]):
    args = argparser.parse_args(argv)

    if args.command == "init":
        cmd_init(args)
    elif args.command == "cat-file":
        cmd_cat_file(args)
    elif args.command == "hash-object":
        cmd_hash_object(args)


def cmd_init(args):
    repo_create(args.path)


def cmd_cat_file(args):
    repo = repo_find()
    cat_file(repo, args.object, fmt=args.type.encode())


def cmd_hash_object(args):
    if args.write:
        repo = GitRepository('.')
    else:
        repo = None

    with open(args.path, 'rb') as fd:
        sha = object_hash(fd, args.type.encode(), repo)
        print(sha)


def cat_file(repo, obj, fmt=None):
    obj = object_read(repo, object_find(repo, obj, fmt=fmt))
    sys.stdout.buffer.write(obj.serialize())


def object_hash(fd, fmt, repo=None):
    data = fd.read()
    if fmt == b'commit':
        obj = GitCommit(repo, data)
    elif fmt == b'tree':
        obj = GitTree(repo, data)
    elif fmt == b'tag':
        obj = GitTag(repo, data)
    elif fmt == b'blob':
        obj = GitBlob(repo, data)
    else:
        raise Exception('Unknown type %s!' % fmt)

    return object_write(obj, repo)
