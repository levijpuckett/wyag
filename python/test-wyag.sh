#!/usr/bin/env bash
set -e

_V=0
while getopts "v" opt; do
    case $opt in
        v)
            _V=1
            ;;
    esac
done
function verbose() {
    [[ $_V -eq 1 ]] && return 0 || return 1
}
verbose && echo "Verbose mode on"

function step() {
    echo $(caller) $@
}

function run_test() {
    cd left
    verbose && echo "git: " && git $@ | tee ../file1 || git $@ > ../file1
    cd ../right
    verbose && echo "wyag: " && $wyag $@ | tee ../file2 || $wyag $@ > ../file2
    cd ..
    cmp file1 file2
    verbose && echo "" || :
}

wyag=$(realpath ./wyag)
echo "testing: $wyag"

testdir=/tmp/wyag-tests
if [[ -e $testdir ]]; then
    rm -rf $testdir/*
else
    mkdir $testdir
fi
cd $testdir
echo "working in: $(pwd)"

step "Create repos"
$wyag init left
git init right > /dev/null

# make sure user name and email lines up for tags and commits.
cd right
git config user.name Nobody
git config user.email no@bo.dy
cd ..

step "status"
cd left
git status > /dev/null
cd ../right
git status > /dev/null
cd ..

step "hash-object"
echo "Don't read me" > README
$wyag hash-object README > hash1
git hash-object README > hash2
cmp --quiet hash1 hash2

step "hash-object -w"
cd left
$wyag hash-object -w ../README > /dev/null
cd ../right
git hash-object -w ../README > /dev/null
cd ..
ls left/.git/objects/b1/7df541639ec7814a9ad274e177d9f8da1eb951 > /dev/null
ls right/.git/objects/b1/7df541639ec7814a9ad274e177d9f8da1eb951 > /dev/null

step "cat-file with blob"
run_test cat-file blob b17d

step "cat-file with long hash"
run_test cat-file blob b17df541639ec7814a9ad274e177d9f8da1eb951

step "Create commit (git only, nothing is tested)" #@FIXME Add wyag commit"
cd left
echo "Aleph" > hebraic-letter.txt
git add hebraic-letter.txt
GIT_AUTHOR_DATE="2010-01-01 01:02:03 +0100" \
               GIT_AUTHOR_NAME="wyag-tests.sh" \
               GIT_AUTHOR_EMAIL="wyag@example.com" \
               GIT_COMMITTER_DATE="2010-01-01 01:02:03 +0100" \
               GIT_COMMITTER_NAME="wyag-tests.sh" \
               GIT_COMMITTER_EMAIL="wyag@example.com" \
               git commit --no-gpg-sign -m "Initial commit" > /dev/null
cd ../right
echo "Aleph" > hebraic-letter.txt
git add hebraic-letter.txt
GIT_AUTHOR_DATE="2010-01-01 01:02:03 +0100" \
               GIT_AUTHOR_NAME="wyag-tests.sh" \
               GIT_AUTHOR_EMAIL="wyag@example.com" \
               GIT_COMMITTER_DATE="2010-01-01 01:02:03 +0100" \
               GIT_COMMITTER_NAME="wyag-tests.sh" \
               GIT_COMMITTER_EMAIL="wyag@example.com" \
               git commit --no-gpg-sign -m "Initial commit" > /dev/null
cd ..

step "cat-file on commit object without indirection"
run_test cat-file commit HEAD

step "cat-file on tree object redirected from commit"
run_test cat-file tree HEAD

step "cat-file with show_type on a commit"
run_test cat-file -t HEAD

step "cat-file with show_type on a blob"
run_test cat-file -t b17d

step "Add some directories and commits (git only, nothing is tested)" #@FIXME Add wyag commit"
cd left
mkdir a
echo "Alpha" > a/greek_letters
mkdir b
echo "Hamza" > a/arabic_letters
git add a/*
GIT_AUTHOR_DATE="2010-01-01 01:02:03 +0100" \
               GIT_AUTHOR_NAME="wyag-tests.sh" \
               GIT_AUTHOR_EMAIL="wyag@example.com" \
               GIT_COMMITTER_DATE="2010-01-01 01:02:03 +0100" \
               GIT_COMMITTER_NAME="wyag-tests.sh" \
               GIT_COMMITTER_EMAIL="wyag@example.com" \
               git commit --no-gpg-sign -m "Commit 2" > /dev/null
cd ../right
mkdir a
echo "Alpha" > a/greek_letters
mkdir b
echo "Hamza" > a/arabic_letters
git add a/*
GIT_AUTHOR_DATE="2010-01-01 01:02:03 +0100" \
               GIT_AUTHOR_NAME="wyag-tests.sh" \
               GIT_AUTHOR_EMAIL="wyag@example.com" \
               GIT_COMMITTER_DATE="2010-01-01 01:02:03 +0100" \
               GIT_COMMITTER_NAME="wyag-tests.sh" \
               GIT_COMMITTER_EMAIL="wyag@example.com" \
               git commit --no-gpg-sign -m "Commit 2" > /dev/null
cd ..

step "ls-tree"
run_test ls-tree HEAD

step "checkout"
# Git and Wyag syntax are different here
cd left
$wyag checkout HEAD ../temp1 > /dev/null
mkdir ../temp2
cd  ../temp2
git --git-dir=../right/.git checkout . > /dev/null
cd ..
diff -r temp1 temp2
rm -rf temp1 temp2

step "lightweight tag"
cd left
$wyag tag TEST
cd ../right
git tag TEST
cd ..
run_test tag

step "annotated tag"
cd left
$wyag tag -a TEST_ANNOTATED
cd ../right
git tag -a TEST_ANNOTATED -m 'blank'
cd ..

# confirms that the list of tags is the same.
run_test tag 
# confirms that the tag object is well structured (except the date!)
run_test cat-file tag TEST_ANNOTATED | grep -v "tagger"

step "rev-parse: named ref"
run_test rev-parse HEAD

step "rev-parse: short hash"
run_test rev-parse 75ee4
run_test rev-parse 8a61

step "rev-parse: branch"
run_test rev-parse main

step "rev-parse: lightweight tag"
run_test rev-parse TEST

#step "rev-parse: annotated tag"
#@FIXME wyag does not tag with date, so we can't compare rev-parse here!
#step "rev-parse (wyag redirection tester)"
#@TODO

step "branch: list"
run_test branch

step "branch: create, no start-point"
run_test branch wyagtest

step "branch: create, with start-point as branch"
run_test branch wyagtest2 wyagtest

step "branch: create, with start-point as past commit"
run_test branch wyagtest3 b664

step "branch: list more"
run_test branch

echo ""
echo "~~~~~~~~~~~~~~~~~~~~~~~~~~~"
echo "All tests passing! Hooray!!"
echo "~~~~~~~~~~~~~~~~~~~~~~~~~~~"
echo ""

# clean up
cd ~
rm -rf $testdir/*
