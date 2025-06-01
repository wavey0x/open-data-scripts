from scripts.prisma import main as prisma_job
from scripts.ybs_dash import main as ybs_job
from scripts.resupply import main as resupply_job
import os, subprocess, datetime
import shutil
from config import (
    YBS_JSON_FILE,
    RESUPPLY_JSON_FILE,
    RAW_BOOST_JSON_FILE,
    get_json_path
)

def main():
    resupply_job.main()
    ybs_job.main()

    destination_dir = '../open-data/'
    os.makedirs(destination_dir, exist_ok=True)

    source_files = [
        get_json_path(YBS_JSON_FILE),
        get_json_path(RAW_BOOST_JSON_FILE),
        get_json_path(RESUPPLY_JSON_FILE),
    ]
    for file in source_files:
        shutil.copy(file, destination_dir)

    print("Files copied successfully.")
    
    if os.getenv('ENV') != 'xdev':
        push_to_gh(destination_dir)

def push_to_gh(project_directory):
    home_dir = os.getenv('HOME')
    key = os.getenv('KEY')
    os.environ['GIT_SSH_COMMAND'] = f'ssh -i {home_dir}/.ssh/{key}' 
    github_repo = 'github.com/wavey0x/open-data.git'
    github_token = os.getenv('GITHUB_PAT')
    remote_url = f'https://{github_token}@{github_repo}'
    os.chdir(project_directory)

    try:
        # Print current working directory
        print(f"Current working directory: {os.getcwd()}")

        # Git status
        subprocess.run(['git', 'status'], check=True)

        # Add the file to staging
        subprocess.run(['git', 'add', '-A'], check=True)

        # Commit the changes
        print(f'Remote URL: {remote_url}')
        current_datetime = datetime.datetime.now()
        formatted_datetime = current_datetime.strftime("%Y-%m-%d %H:%M:%S")
        commit_message = f'automated data write: {formatted_datetime}'

        subprocess.run(['git', 'commit', '-m', commit_message], check=True)

        # Push the changes
        subprocess.run(['git', 'push', remote_url, 'master', '--force'], check=True)

        print("Changes committed and pushed to GitHub successfully.")

    except subprocess.CalledProcessError as e:
        print(f"An error occurred: {e}")

def fetch_from_gh(project_directory):
    home_dir = os.getenv('HOME')
    key = os.getenv('KEY')
    os.environ['GIT_SSH_COMMAND'] = f'ssh -i {home_dir}/.ssh/{key}' 
    os.chdir(project_directory)
    try:
        # Add the file to staging
        subprocess.run(['git', 'fetch', '--all'], check=True)
        subprocess.run(['git', 'reset', '--hard', 'origin/master'], check=True)
        print("Local project synced")

    except subprocess.CalledProcessError as e:
        print(f"An error occurred: {e}")
