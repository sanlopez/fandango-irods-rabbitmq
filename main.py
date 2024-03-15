import pika
import json
import os
import configparser
from irods.session import iRODSSession
from irods.ticket import Ticket
from db.utils import create_new_collection

config = configparser.ConfigParser()
config.read(os.path.join(os.path.dirname(__file__), 'config.yaml'))

irods_zone = config['ZONE'].get('NAME')
irods_host = config['ZONE'].get('HOST')
irods_user = config['ZONE'].get('USER')
irods_port = config['ZONE'].get('PORT')
irods_pass = config['ZONE'].get('PASS')
irods_parent_collection = config['ZONE'].get('COLLECTION')


def load_args_schema(action):
    with open('args_schema.json') as f:
        args_schema = json.load(f)
    return args_schema.get(action, [])


def execute_function(action, provided_args):
    # check if required args are provided by the user
    required_args = load_args_schema(action)
    if required_args:
        if all(arg['name'] in provided_args for arg in required_args if arg['required']):
            # depending on the action, do one thing or another
            if action == 'copyData':
                success, info = copy_data(provided_args['projectId'], provided_args['rawData'], True if provided_args.get('writePermission', '').lower() == 'yes' else False)
        else:
            print(f"Not all required args ({', '.join(required_args)}) provided for action {action}")
    else:
        print('Non valid action provided')
    return success, info


def copy_data(project_id, raw_data_path, write_permission=False):
    """
    Function that creates an iRODS collection from data provided

    Args:
        project_id (int): FandanGO project ID
        raw_data_path (str): path of the origin data

    Returns:
        success (bool): if everything went ok
        info (dict): dict containing relevant info (such as ticket id)
    """

    print(
        f'FandanGO will create an iRODS collection for projectID {project_id} with rawData located at {raw_data_path}...')
    success = False
    info = {}

    with iRODSSession(host=irods_host, port=irods_port, user=irods_user, password=irods_pass,
                      zone=irods_zone) as session:
        try:
            # create new collection and put the data onto it
            new_collection = irods_parent_collection + project_id
            session.collections.create(new_collection)
            for file_name in os.listdir(raw_data_path):
                local_file_path = os.path.join(raw_data_path, file_name)
                session.data_objects.put(local_file_path, new_collection + '/' + file_name)

            # create ticket for retrieving the data
            print(f'Creating ticket for project {project_id}...')
            new_ticket = Ticket(session)
            ticket_id = new_ticket.issue(permission='write' if write_permission else 'read',
                                         target=new_collection).string
            print(f'... ticket generated with id {ticket_id}...')
            info = {'ticket_id': ticket_id}

            # update ddbb with collection info
            create_new_collection(project_id, new_collection, ticket_id)
            success = True

        except Exception as e:
            print(f'... collection could not be created for project {project_id} because of: {e}')
    return success, info


def callback(ch, method, properties, body):
    args = eval(body.decode())
    success, info = execute_function(args['action'], args['args'])
    result = {'success': success, 'message': info}
    # send results to core's queue
    ch.basic_publish(exchange='', routing_key='core', body=json.dumps(result))


def main():
    # create RabbitMQ connection
    connection = pika.BlockingConnection(pika.ConnectionParameters('localhost'))
    channel = connection.channel()
    # irods' queue. This will listen for core plugin requests
    channel.queue_declare(queue='irods')
    # irods' queue start listening
    channel.basic_consume(queue='irods', on_message_callback=callback, auto_ack=True)
    print('Waiting for messages...')
    channel.start_consuming()


if __name__ == '__main__':
    main()
