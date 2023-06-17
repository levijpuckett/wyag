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
    fmt = b''

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


class GitCommit(GitObject):
    fmt = b'commit'

    def serialize(self):
        return kvlm_serialize(self.kvlm)

    def deserialize(self, data):
        self.kvlm = kvlm_parse(data)


class GitTag(GitCommit):
    # all we need to do to support tags is change the object format to 'tag' from 'commit'
    fmt = b'tag'


class GitTreeLeaf(object):
    def __init__(self, mode, path, sha):
        self.mode = mode
        self.path = path
        self.sha = sha


class GitTree(GitObject):
    fmt = b'tree'

    def deserialize(self, data):
        self.items = tree_parse(data)

    def serialize(self):
        return tree_serialize(self)

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
        if os.path.isdir(path):
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


# def find_named_ref(repo, name, refs=None):
def resolve_named_ref(repo, name):
    for path in [name, f'refs/heads/{name}', f'refs/remotes/{name}', f'refs/tags/{name}']:
        if os.path.isfile(repo_file(repo, path)):
            return ref_resolve(repo, path)


def resolve_shorthash(repo, name):
    hash_re = re.compile(r'^[0-9A-Fa-f]{4,40}$')
    if hash_re.match(name):
        if len(name) == 40:
            return name.lower()

        name = name.lower()
        prefix = name[:2]
        path = repo_dir(repo, 'objects', prefix)
        if path:
            remainder = name[2:]
            for f in os.listdir(path):
                if f.startswith(remainder):
                    return prefix + f


def object_resolve(repo, name):
    # return the sha of an object, referred to by name, short hash, or full hash.
    sha = resolve_shorthash(repo, name)
    if sha:
        return sha

    sha = resolve_named_ref(repo, name)
    return sha


def object_find(repo, name, fmt=None, follow=True):
    sha = object_resolve(repo, name)
    if not sha:
        raise Exception(f'No such reference: {name}')
    if not fmt:
        return sha

    while True:
        obj = object_read(repo, sha)
        if obj.fmt == fmt:
            return sha
        if not follow:
            return None

        if obj.fmt == b'tag':
            sha = obj.kvlm[b'object'].decode('ascii')
        elif obj.fmt == b'commit' and fmt == b'tree':
            sha = obj.kvlm[b'tree'].decode('ascii')
        else:
            return None


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


def kvlm_parse(raw, start=0, dct=None):
    """Parse a 'key-value list with a message.
    Format:
    <key><space><value><newline>
    values may have newlines, in which case the new line shall be indented by one space
    the message is the last part of the format, and is separated from the rest with a blank newline.
    """
    if not dct:
        dct = collections.OrderedDict()
    # search for the next space, and the next newline
    spc = raw.find(b' ', start)
    nl = raw.find(b'\n', start)

    # if no space was found OR a new line was encountered before a space, then this should be a blank line.
    if (spc < 0) or (nl < spc):
        assert(nl == start)
        dct[b''] = raw[start+1:]
        return dct

    # otherwise, recurse...
    key = raw[start : spc] # start -> space will be the key
    end = start
    while True:
        end = raw.find(b'\n', end+1)
        if raw[end+1] != ord(' '):
            break

    # parse out the value, including dropping leading spaces on new lines.
    value = raw[spc+1:end].replace(b'\n ', b'\n')
    if key in dct:
        if type(dct[key]) == list:
            dct[key].append(value)
        else:
            dct[key] = [dct[key], value]
    else:
        dct[key] = value

    # keep parsing
    return kvlm_parse(raw, start=end+1, dct=dct)


def kvlm_serialize(kvlm):
    ret = b''
    for k in kvlm.keys():
        # skip the message
        if k == b'':
            continue
        val = kvlm[k]
        # normalize to a list
        if type(val) != list:
            val = [val]
        for v in val:
            ret += k + b' ' + (v.replace(b'\n', b'\n ')) + b'\n'
    ret += b'\n' + kvlm[b'']
    return ret


def tree_parse_one(raw, start=0):
    # A tree object is formatted like this:
    # <mode><space><path><null><sha>
    x = raw.find(b' ', start)
    assert(x - start == 5 or x - start == 6)

    # before the space is the mode
    mode = raw[start:x]

    # find the path
    y = raw.find(b'\x00', x)
    path = raw[x+1:y]

    sha = format(int.from_bytes(raw[y+1:y+21], 'big'), '040x')

    return y+21, GitTreeLeaf(mode, path, sha)


def tree_parse(raw):
    pos = 0
    max = len(raw)
    ret = list()
    while pos < max:
        pos, data = tree_parse_one(raw, pos)
        ret.append(data)
    return ret


def tree_serialize(obj):
    ret = b''
    for i in obj.items:
        ret += i.mode
        ret += b' '
        ret += i.path
        ret += b'\x00'
        sha = int(i.sha, 16)
        ret += sha.to_bytes(20, byteorder='big')
    return ret


def tree_walk(obj: GitObject, recurse=False, path=''):
    """Print a tree object and optionally recurse down into the given
    tree SHA until we reach a blob."""
    for item in obj.items:
        subobj = object_read(obj.repo, item.sha)
        if recurse and (type(subobj) == GitTree):
            tree_walk(subobj, recurse=True,
                      path=os.path.join(path, item.path.decode()))
        else:
            print('{0} {1} {2}\t{3}'.format(
                '0' * (6 - len(item.mode)) + item.mode.decode('ascii'),
                # git's ls-tree displays the type of the object pointed to.
                # Let's do that too :)
                subobj.fmt.decode('ascii'),
                item.sha,
                path + item.path.decode('ascii')
            ))


def ref_resolve(repo, ref):
    # resolve a ref by recursing until we hit a SHA1 hash
    with open(repo_file(repo, ref), 'r') as fp:
        # the last character is '\n' so drop it
        data = fp.read()[:-1]
    if data.startswith('ref: '):
        return ref_resolve(repo, data[5:])
    else:
        return data


def ref_list(repo, path=None) -> collections.OrderedDict:
    if not path:
        path = repo_dir(repo, 'refs')

    # ordered dict to sort the refs like git.
    ret = collections.OrderedDict()
    for f in sorted(os.listdir(path)):
        candidate = os.path.join(path, f)
        if os.path.isdir(candidate):
            ret[f] = ref_list(repo, candidate)
        else:
            ret[f] = ref_resolve(repo, candidate)
    return ret


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
                   nargs='?',
                   choices=["blob", "commit", "tag", "tree"],
                   help="Specify the type")
argsp.add_argument('-t',
                   dest='show_type',
                   action='store_true',
                   help='show the type of the object.')
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

# subparser for log
argsp = argsubparsers.add_parser('log', help='Display history of a given commit.')
argsp.add_argument('commit',
                   default='HEAD',
                   nargs='?',
                   help='Commit to start at.')

# subparser for ls-tree
argsp = argsubparsers.add_parser('ls-tree', help='Pretty-print a tree object.')
argsp.add_argument('object',
                   help='The object to show.')

argsp.add_argument('-r',
                   dest='recurse',
                   action='store_true',
                   help='Recurse into subtrees')

argsp = argsubparsers.add_parser('checkout', help='Checkout a commit into the given directory.')

argsp.add_argument('commit',
                   help='The commit or tree to checkout.')

argsp.add_argument('path',
                   help='The EMPTY directory to checkout on.')

argsp = argsubparsers.add_parser('show-ref', help='list references.')

argsp = argsubparsers.add_parser('tag', help='List and create tags')
argsp.add_argument('-a',
                   action='store_true',
                   dest='create_tag_object',
                   help='Create an annotated tag.')
argsp.add_argument('name',
                   nargs='?',
                   help='The new tag\'s name')
argsp.add_argument('object',
                   default='HEAD',
                   nargs='?',
                   help='The object the new tag will point to')

argsp = argsubparsers.add_parser('rev-parse',
                                 help='Parse revision (or other objects) identifiers')
argsp.add_argument('--wyag-type',
                   metavar='type',
                   dest='type',
                   choices=['blob', 'commit', 'tag', 'tree'],
                   default=None,
                   help='Specify the expected type')
argsp.add_argument('name',
                   help='The name to parse')

argsp = argsubparsers.add_parser('branch',
                                 help='Create or list branches.')
argsp.add_argument('name',
                   nargs='?',
                   help='name for the new branch')
argsp.add_argument('startpoint',
                   default='HEAD',
                   nargs='?',
                   help='The commit to start the new branch on.')


def main(argv=sys.argv[1:]):
    args = argparser.parse_args(argv)

    if args.command == "init":
        cmd_init(args)
    elif args.command == "cat-file":
        cmd_cat_file(args)
    elif args.command == "hash-object":
        cmd_hash_object(args)
    elif args.command == 'log':
        cmd_log(args)
    elif args.command == 'ls-tree':
        cmd_ls_tree(args)
    elif args.command == 'checkout':
        cmd_checkout(args)
    elif args.command == 'show-ref':
        cmd_show_ref(args)
    elif args.command == 'tag':
        cmd_tag(args)
    elif args.command == 'rev-parse':
        cmd_rev_parse(args)
    elif args.command == 'branch':
        cmd_branch(args)


def cmd_init(args):
    repo_create(args.path)


def cmd_cat_file(args):
    repo = repo_find()
    if args.show_type:
        sha = object_find(repo, args.object, follow=False)
        print(object_read(repo, sha).fmt.decode('ascii'))
    else:
        cat_file(repo, args.object, fmt=args.type.encode())


def cmd_hash_object(args):
    if args.write:
        repo = GitRepository('.')
    else:
        repo = None

    with open(args.path, 'rb') as fd:
        sha = object_hash(fd, args.type.encode(), repo)
        print(sha)


def cmd_log(args):
    repo = repo_find()
    print('digraph wyaglog{')
    log_graphviz(repo, object_find(repo, args.commit), set())
    print('}')


def cmd_ls_tree(args):
    repo = repo_find()
    obj = object_read(repo, object_find(repo, args.object, fmt=b'tree'))
    if type(obj) != GitTree:
        raise Exception(f'"object" must be a tree, but is a {obj.fmt.decode("ascii")}')

    assert (type(obj) == GitTree)
    tree_walk(obj, recurse=args.recurse)


def cmd_checkout(args):
    repo = repo_find()
    obj = object_read(repo, object_find(repo, args.commit))

    # if the object is a commit, we grab its tree
    if obj.fmt == b'commit':
        obj = object_read(repo, obj.kvlm[b'tree'].decode('ascii'))

    # verify that path is an empty directory
    if os.path.exists(args.path):
        if not os.path.isdir(args.path):
            raise Exception('Not a directory {0}!'.format(args.path))
        if os.listdir(args.path):
            raise Exception('{0} is not empty!'.format(args.path))
    else:
        os.makedirs(args.path)

    tree_checkout(repo, obj, os.path.realpath(args.path).encode())


def cmd_show_ref(args):
    repo = repo_find()
    refs = ref_list(repo)
    show_ref(repo, refs, prefix='refs')


def cmd_tag(args):
    repo = repo_find()

    if args.name:
        # a name is provided, we're creating a new tag.
        tag_create(repo, args.name, args.object,
                   annotated_tag=True if args.create_tag_object else False)
    else:
        # no name? just list the tags.
        refs = ref_list(repo)
        show_ref(repo, refs['tags'], with_hash=False)


def cmd_branch(args):
    repo = repo_find()
    if args.name:
        # make a new branch with this name
        sha = object_find(repo, args.startpoint)
        if not sha:
            raise Exception('Bad reference! Could not resolve object to tag!')
        # create a branch (just a ref!)
        ref_create(repo, os.path.join('heads', args.name), sha)
    else:
        # just list the branches, and star the branch we currently have checked out (if any)
        refs = ref_list(repo)
        asterisk = ''
        with open(repo_file(repo, 'HEAD'), 'r') as f:
            data = f.read()
        if data.startswith('ref: refs/heads/'):
            asterisk = data[16:-1]  # grab just the branch name and strip off the newline character.
        else:
            asterisk = ' '
            print(f'* (HEAD detached at {data[0:7]})')
        show_ref(repo, refs['heads'], with_hash=False, asterisk=asterisk)


def cmd_rev_parse(args):
    if args.type:
        fmt = args.type.encode()
    else:
        fmt = None

    repo = repo_find()
    print(object_find(repo, args.name, fmt, follow=True))


def tag_create(repo, name, reference, annotated_tag):
    # get the GitObject from the object reference
    sha = object_find(repo, reference)
    if not sha:
        raise Exception('Bad reference! Could not resolve object to tag!')

    if annotated_tag:
        # create a tag object, which is formed like a commit (except with different keys).
        # The keys are:
        #   - object: the sha of the object we're tagging (usually a commit,
        #              but actually you can tag any object you want)
        #   - type: the type of object you are tagging
        #   - tag: the name of the tag
        #   - tagger: like committer: the user doing the tagging
        #   - message: A "custom" message like a commit message
        tag = GitTag(repo)
        tag.kvlm = collections.OrderedDict()
        tag.kvlm[b'object'] = sha.encode()
        tag.kvlm[b'type'] = b'commit'
        tag.kvlm[b'tag'] = name.encode()
        tag.kvlm[b'tagger'] = b'Nobody <no@bo.dy>'
        tag.kvlm[b''] = b'A tag generated by wyag - not customizable :)\n'
        tag_sha = object_write(tag, repo)
        ref_create(repo, os.path.join('tags', name), tag_sha)
    else:
        # create a lightweight tag (just a ref!)
        ref_create(repo, os.path.join('tags', name), sha)


def ref_create(repo, ref_name, sha):
    with open(repo_file(repo, os.path.join('refs', ref_name)), 'w') as f:
        f.write(sha + '\n')


def show_ref(repo: GitRepository, refs: collections.OrderedDict, with_hash=True, prefix='', asterisk=''):
    for ref_name, sha in refs.items():
        if type(sha) == str:
            output = '{0}{1}{2}'.format(
                sha + ' ' if with_hash else '',
                prefix + '/' if prefix else '',
                ref_name)
            if asterisk != '':
                output = '* ' + output if output == asterisk else '  ' + output
            print(output)
        else:
            show_ref(repo, sha, with_hash=with_hash, prefix="{0}{1}{2}".format(prefix, '/' if prefix else '', ref_name),
                                                                               asterisk=asterisk)


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


def log_graphviz(repo, sha, seen):
    '''
    Generate a "log" in the graphviz format.
    '''
    if sha in seen:
        return
    seen.add(sha)
    commit = object_read(repo, sha)
    assert (commit.fmt == b'commit')
    if not b'parent' in commit.kvlm.keys():
        return

    parents = commit.kvlm[b'parent']

    if type(parents) != list:
        parents = [parents]

    for p in parents:
        p = p.decode('ascii')
        print('c_{0} -> c_{1};'.format(sha, p))
        log_graphviz(repo, p, seen)


def tree_checkout(repo, tree, path):
    for item in tree.items:
        # grab the object
        obj = object_read(repo, item.sha)
        # and the path to it (relative to the root git dir)
        dest = os.path.join(path, item.path)

        if obj.fmt == b'tree':
            # if it's a tree, make a directory and recurse into it
            os.mkdir(dest)
            tree_checkout(repo, obj, dest)
        elif obj.fmt == b'blob':
            # if it's a blob, then 'dest' is a file name, and let's unpack the blob into it.
            with open(dest, 'wb') as f:
                f.write(obj.blobdata)