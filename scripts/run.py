from scripts.prisma import main as prisma_job
from scripts.ybs_dash import main as ybs_job
import os, subprocess, datetime
import shutil

def main():
    ybs_job.main()
    # prisma_job.main()

    destination_dir = '../open-data/'
    os.makedirs(destination_dir, exist_ok=True)

    source_files = ['./data/ybs_data.json', './data/prisma_liquid_locker_data.json']
    for file in source_files:
        shutil.copy(file, destination_dir)

    print("Files copied successfully.")
    
    if os.getenv('ENV') != 'xdev':
        push_to_gh(destination_dir)

def push_to_gh(project_directory):
    home_dir = os.getenv('HOME')
    key = os.getenv('KEY')
    os.environ['GIT_SSH_COMMAND'] = f'ssh -i {home_dir}/.ssh/{key}' 

    os.chdir(project_directory)

    # Git commands to commit and push the changes
    try:
        # Add the file to staging
        subprocess.run(['git', 'add', '-A'], check=True)

        # Commit the changes
        current_datetime = datetime.datetime.now()
        formatted_datetime = current_datetime.strftime("%Y-%m-%d %H:%M:%S")
        commit_message = f'automated data write: {formatted_datetime}'

        subprocess.run(['git', 'commit', '-m', commit_message], check=True)

        # Push the changes
        subprocess.run(['git', 'push', 'origin', 'master', '--force'], check=True)

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