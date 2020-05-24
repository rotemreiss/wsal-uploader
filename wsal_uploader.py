#!/usr/bin/python
# coding=utf-8

import argparse
import mysql.connector
import os.path
import boto3
from botocore.exceptions import ClientError
import json
import socket


def get_last_log_id():
    id = 0
    global log_track_path

    try:
        if os.path.exists(log_track_path):
            f = open(log_track_path, "r")
            line = f.readline()

            if line.isnumeric():
                id = line
            else:
                print ("[-] Illegal log id. log_track being set to 0.")
                save_last_log_id(0)

    except Exception as e:
        print(e)

    return id


def save_last_log_id(id):
    global log_track_path

    try:
        f = open(log_track_path, "w")
        f.write(str(id))
        f.close()
    except Exception as e:
        print(e)


def db_init():
    global db_connector
    db_config = {
        'host': config.db_host,
        'user': config.db_user,
        'passwd': config.db_passwd,
        'database': config.db_database
    }

    if hasattr(config, 'db_extra_config'):
        db_config = {**db_config, **config.db_extra_config}

    db_connector = mysql.connector.connect(**db_config)


def fetch_occurrences(offset):
    global db_connector
    cursor = db_connector.cursor()

    cursor.execute("SELECT id, created_on, alert_id FROM wp_wsal_occurrences"
                   " WHERE id > %s"
                   " ORDER BY id ASC", (offset, ))

    return cursor.fetchall()


def fetch_metadata(offset):
    global db_connector
    cursor = db_connector.cursor()

    cursor.execute("SELECT occurrence_id, name, value FROM wp_wsal_metadata"
                   " WHERE occurrence_id > %s"
                   " ORDER BY occurrence_id ASC", (offset, ))

    return cursor.fetchall()


def prepare_export_struct(occurrences, metadata):
    ex_struct = []

    for o in occurrences:
        item = {}

        item['id'] = o[0]
        item['created_on'] = o[1]
        item['alert_id'] = o[2]
        for m in metadata:
            if m[0] == o[0]:
                item[m[1]] = m[2]

        ex_struct.append(item)

    return ex_struct


def upload_json_to_s3(data, last_id):
    global base_path

    file_name = f'wp-audit-logs-{args.config_file}-{last_id}.json'
    file_path = f'{base_path}/logs/{file_name}'

    # Export the json
    with open(file_path, 'w') as outfile:
        json.dump(data, outfile)

    if not args.is_dry:
        s3_client = boto3.client(
            's3',
            aws_access_key_id=config.aws_access_key_id,
            aws_secret_access_key=config.aws_secret_access_key
        )

        # Upload the file
        hostname = socket.gethostname()
        object_name = f'wordpress/{config.website_domain}/{hostname}--{file_name}'

        try:
            response = s3_client.upload_file(file_path, config.aws_bucket, object_name)
        except ClientError as e:
            print(e)
            return False

    return True


def file_path(path):
    if os.path.isfile(f"{path}.py"):
        return path
    else:
        raise argparse.ArgumentTypeError(f"readable_file:{path} is not a valid path")


def main():
    global base_path
    global log_track_path
    base_path = os.path.dirname(os.path.realpath(__file__))
    log_track_path = base_path + "/log_track_" + args.config_file

    # Get the id of the last log item as our offset
    log_id = get_last_log_id()

    db_init()
    try:
        occ = fetch_occurrences(log_id)

        if occ:
            new_last_id = occ[-1][0]
            md = fetch_metadata(log_id)

            ex_struct = prepare_export_struct(occ, md)
            upload_json_to_s3(ex_struct, new_last_id)

            if not args.is_dry:
                save_last_log_id(new_last_id)
                print (f'[+] Updated the log file. Last ID is now: {new_last_id}')
            else:
                print (f'[+] Dry run, nothing was uploaded.')
        else:
            print ("[-] Nothing to update.")

    except Exception as e:
        print(e)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Ships WordPress security audit logs to Amazon S3 bucket.')
    parser.add_argument('-c', '--config', help='Path for another config file (without the py extension).', type=file_path, dest='config_file', default='config')
    parser.add_argument('--dry-run', help='Do not update the logs delta and do not ship it to S3.', action='store_true', dest='is_dry')

    args = parser.parse_args()

    # Import our configuration file
    config = __import__(args.config_file)

    main()
