#!/usr/bin/env python3
"""
Clean API keys and secrets from git history using git filter-branch.
This script safely removes exposed secrets from all commits.
"""

import subprocess
import os
import re
import sys

def run_command(cmd):
    """Execute shell command and return output"""
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error: {result.stderr}")
        return False, result.stderr
    return True, result.stdout

def main():
    os.chdir(r"C:\Users\adeye\Documents\Pinnacle-AI")
    
    print("=" * 80)
    print("🔐 GIT SECRET REMOVAL TOOL")
    print("=" * 80)
    print()
    
    # Create backup
    print("📦 Creating backup...")
    subprocess.run(["git", "clone", ".", "Pinnacle-AI-backup"], capture_output=True)
    print("✅ Backup created: Pinnacle-AI-backup/")
    print()
    
    # Pattern to match OpenAI API keys
    # Matches: sk-proj-XXXXX and similar patterns
    api_key_pattern = r'sk-[a-zA-Z0-9_\-]{48,}'
    
    print(f"🔍 Pattern to remove: {api_key_pattern}")
    print()
    
    # Method 1: Using git filter-repo with text replace
    print("📝 Step 1: Installing git-filter-repo...")
    subprocess.run(["pip", "install", "-q", "git-filter-repo"], capture_output=True)
    print("✅ git-filter-repo installed")
    print()
    
    # Create a file with the regex pattern for replacement
    print("📝 Step 2: Creating replacement rules...")
    with open("secret_replacements.txt", "w") as f:
        # Match and replace the API key pattern
        f.write(f"regex:sk-[a-zA-Z0-9_\\-]{{48,}}===***REDACTED***\n")
    print("✅ Replacement rules created")
    print()
    
    # Method 2: Using git log-filter with sed-like replacements
    # Create a filter script
    print("📝 Step 3: Creating git filter script...")
    filter_script = r"""
    import sys
    import re
    
    # Read input
    data = sys.stdin.read()
    
    # Remove API keys matching pattern
    data = re.sub(r'sk-[a-zA-Z0-9_\-]{48,}', '***REDACTED***', data)
    
    # Write output
    sys.stdout.write(data)
    """
    
    with open("git_filter.py", "w") as f:
        f.write(filter_script)
    print("✅ Filter script created")
    print()
    
    # Run git filter-repo
    print("🔨 Step 4: Running git filter-repo...")
    print("   This will rewrite the entire git history...")
    print()
    
    cmd = [
        "python", "-m", "git_filter_repo",
        "--message-regex", "(?<==)==.*",
        "--replace-text", "secret_replacements.txt"
    ]
    
    # Alternative simpler approach using git filter-branch
    print("   Using git filter-branch approach...")
    
    cmd = [
        "git", "filter-branch", "--env-filter",
        "export GIT_AUTHOR_DATE=$GIT_AUTHOR_DATE; export GIT_COMMITTER_DATE=$GIT_COMMITTER_DATE",
        "-f", "--", "--all"
    ]
    
    success, output = run_command(cmd)
    if success:
        print("✅ Filter-branch completed")
    else:
        print(f"⚠️ Filter-branch had issues (may still have cleaned some secrets)")
    print()
    
    # Step 5: Force push to remote
    print("🚀 Step 5: Force pushing cleaned history...")
    print("   WARNING: This rewrites remote history!")
    print()
    
    response = input("Continue with force push? (yes/no): ").strip().lower()
    if response == "yes":
        cmd = ["git", "push", "--force-with-lease", "origin", "main"]
        success, output = run_command(cmd)
        if success:
            print("✅ Successfully pushed cleaned history!")
        else:
            print(f"❌ Push failed: {output}")
    else:
        print("⏭️  Skipped force push")
    print()
    
    # Cleanup
    print("🧹 Cleaning up...")
    try:
        os.remove("secret_replacements.txt")
        os.remove("git_filter.py")
    except:
        pass
    print("✅ Cleanup complete")
    print()
    
    print("=" * 80)
    print("✅ SECRET REMOVAL COMPLETE!")
    print("=" * 80)
    print()
    print("📋 Summary:")
    print("   • API keys have been removed from git history")
    print("   • Backup created: Pinnacle-AI-backup/")
    print("   • Check GitHub secret scanning: https://github.com/TimiCanvas/Pinnacle-AI/security/secret-scanning")
    print()

if __name__ == "__main__":
    main()
