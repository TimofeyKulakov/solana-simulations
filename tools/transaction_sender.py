import asyncio
import aiohttp
import datetime
import json
import requests
import argparse
import time
from typing import cast
from statistics import mean
import solana

from solana.system_program import TransferParams, transfer
from solana.transaction import Transaction
from solana.account import Account
from solana.rpc.api import Client
from solana.blockhash import Blockhash

from solana.rpc.types import RPCMethod, RPCResponse
from base64 import b64encode


async def post_transaction(session, url, params, request_id, current_slot):
    headers = {"Content-Type": "application/json"}
    method = RPCMethod("sendTransaction")
    data = json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method,
                       "params": [params, {"skipPreflight": True,
                                           "preflightCommitment": "max", "encoding": "base64"}]})
    async with session.post(url, headers=headers, data=data) as response:
        response_text = await response.text()
        # adding transaction to validating_set
        validating_list[json.loads(response_text)["result"]] = {'sent_slot': current_slot,
                                                                'commitment_slot': None}


async def post_transaction_checker(session, url, tx_sign, request_id):
    headers = {"Content-Type": "application/json"}
    method = RPCMethod("getConfirmedTransaction")
    data = json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method,
                       "params": [tx_sign, "json"]})
    async with session.post(url, headers=headers, data=data) as response:
        response_text = await response.text()
        if json.loads(response_text)["result"] is None:
            validating_list[tx_sign]['commitment_slot'] = None
        else:
            validating_list[tx_sign]['commitment_slot'] = json.loads(response_text)["result"]['slot']


async def experiment_checker():
    async with aiohttp.ClientSession() as session:
        post_tasks = []
        request_id = 3
        for txn in validating_list.keys():
            request_id += 1
            post_tasks.append(post_transaction_checker(session, host, txn, request_id))
        await asyncio.gather(*post_tasks)


async def batch_sender(batch, current_slot):
    async with aiohttp.ClientSession() as session:
        post_tasks = []
        request_id = 3  # each request must be with unique request_id
        for txn in batch:
            request_id += 1
            if isinstance(txn, bytes):
                txn = b64encode(txn).decode("utf-8")
            post_tasks.append(post_transaction(session, host, txn, request_id, current_slot))
        await asyncio.gather(*post_tasks)


def airdrop_request(url, pubkey, value):
    request_id = 1
    method = RPCMethod("requestAirdrop")
    headers = {"Content-Type": "application/json"}
    params = [str(pubkey), value, {"commitment": "max"}]
    data = json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
    print(data, requests.post(url, headers=headers, data=data))


def get_recent_blockhash(url):
    request_id = 2
    method = RPCMethod("getRecentBlockhash")
    headers = {"Content-Type": "application/json"}
    params = [{"commitment": "max"}]
    data = json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
    try:
        raw_response = requests.post(url, headers=headers, data=data)
        return cast(RPCResponse, json.loads(raw_response.text))
    except:
        print("cannot get blockhash :", data)
        return None


def create_batch_transactions(n=10, sender=Account(4), recipient=Account(5), lamports=10000):
    blockhash_response = get_recent_blockhash(host)
    blockhash = Blockhash(blockhash_response["result"]["value"]["blockhash"])
    print(datetime.datetime.now() - start, "start creating batch")
    batch_transactions = []
    for i in range(n):
        tx = Transaction().add(transfer(TransferParams(from_pubkey=sender.public_key(),
                                                       to_pubkey=recipient.public_key(), lamports=lamports + i)))
        tx.recent_blockhash = blockhash
        tx.sign(sender)
        batch_transactions.append(tx.serialize())
    return batch_transactions, blockhash_response['result']['context']['slot']


def check_transactions():
    incorrect_transaction_cnt = 0
    latency = []
    time.sleep(20)
    print("balance recipient:", hc.get_balance(recipient.public_key())['result'])

    asyncio.run(experiment_checker())

    for transaction in validating_list.keys():
        if validating_list[transaction]['commitment_slot'] is None:
            incorrect_transaction_cnt += 1
        else:
            latency.append(validating_list[transaction]['commitment_slot'] - validating_list[transaction]['sent_slot'])
    print("Success:", len(latency),
          "Error:", incorrect_transaction_cnt)
    if len(latency):
        print('Mean latency:', mean(latency), 'slots')
    with open("transaction_logger.txt", "a") as file:
        file.write(host+'\n')
        file.write("{} Success: {} Error: {}\n".format(start, len(latency), incorrect_transaction_cnt))
        if len(latency):
            file.write('Mean latency: {} slots\n'.format(mean(latency)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Run Velas performance test')
    parser.add_argument('--tps', default=10, type=int, help='tps (batch transactions)')
    parser.add_argument('--host', type=str, default="http://devnet.solana.com", help='host')
    parser.add_argument('--airdrop', default=1234567890, type=int, help='airdrop value')
    parser.add_argument('--s', default=1, type=int, help='duration of experiment in seconds')
    args = parser.parse_args()

    host = args.host + ":8899"
    start = datetime.datetime.now()
    print("START : ", start)
    hc = Client(host)
    sender, recipient = Account(6), Account(7)
    airdrop_request(host, sender.public_key(), args.airdrop)
    time.sleep(5)
    print("balance sender:", hc.get_balance(sender.public_key())['result'])
    print("balance recipient:", hc.get_balance(recipient.public_key())['result'])

    validating_list = {}
    for second in range(args.s):
        tx_list, slot = create_batch_transactions(args.tps, sender, recipient) # add slot to valid dict
        print(datetime.datetime.now() - start, "start sending batch of {} part {}".format(args.tps, second+1))
        asyncio.run(batch_sender(tx_list, slot))
        print(datetime.datetime.now() - start, "batch is sent")
        time.sleep(0.5)
    print(datetime.datetime.now() - start, "experiment is sent")

    check_transactions()

    print("END : ", datetime.datetime.now())


# python tools/transaction_sender.py --tps 100 --s 3"