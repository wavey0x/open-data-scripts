import warnings

# Silence noisy web3/pkg_resources deprecation warning
warnings.filterwarnings("ignore", category=UserWarning, module="web3")

from scripts.ybs_dash import main as ybs_job
from scripts.ybs_dash.listeners import event_listener
from scripts.resupply import main as resupply_job
from scripts.resupply import position_monitor
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
    destination_dir = '../open-data/'
    os.makedirs(destination_dir, exist_ok=True)

    chart_dir = os.path.join(destination_dir, "charts")
    chart_path = os.path.join(chart_dir, "resupply_positions.png")
    meta_path = os.path.join(chart_dir, "resupply_positions_meta.json")
    position_monitor.main(output_path=chart_path, meta_path=meta_path)
    ybs_job.main()
    event_listener.main()

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
    bot_name = os.getenv("BOT_GIT_NAME", "wavey0x-bot")
    bot_email = os.getenv("BOT_GIT_EMAIL", "wavey0x-bot@proton.me")
    os.chdir(project_directory)

    try:
        # Print current working directory
        print(f"Current working directory: {os.getcwd()}")

        # Git status
        subprocess.run(['git', 'status'], check=True)

        # Add the file to staging
        subprocess.run(['git', 'add', '-A'], check=True)

        # Commit the changes
        os.environ['GIT_AUTHOR_NAME'] = bot_name
        os.environ['GIT_AUTHOR_EMAIL'] = bot_email
        os.environ['GIT_COMMITTER_NAME'] = bot_name
        os.environ['GIT_COMMITTER_EMAIL'] = bot_email
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
