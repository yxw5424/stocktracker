import pymongo
import logging

import urllib.parse
import os

class DatabaseHandler:
    _instance = None
    DEFAULT_MONGO_DB = None
    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(DatabaseHandler, cls).__new__(cls)
        return cls._instance

    def __init__(self, config):
        if not hasattr(self, 'initialized'):  # Prevent re-initialization
            self.DEFAULT_MONGO_DB = config['MONGO_DB']
            # 对密码进行URL编码，避免特殊字符导致的认证问题
            encoded_password = urllib.parse.quote_plus(config["MONGO_PASSWORD"])

            # 构建连接字符串
            _auth = (config["MONGO_USER"] + ":" + encoded_password + "@") if config.get("MONGO_USER") else ""
            MONGO_URI = f'mongodb://{_auth}{config["MONGO_URI"]}/{config["MONGO_AUTH_DB"]}'
            if (config['MONGO_TYPE']=='single'):
                self.mongo_client = pymongo.MongoClient(
                    MONGO_URI,
                    readPreference='secondaryPreferred',  # 优先从从节点读取
                    w='majority',  # 写入确认级别
                    retryWrites=True,  # 自动重试写操作
                    socketTimeoutMS=30000,  # 套接字超时时间
                    connectTimeoutMS=20000,  # 连接超时时间
                    serverSelectionTimeoutMS=30000,  # 服务器选择超时时间
                    authSource=config["MONGO_AUTH_DB"],  # 明确指定认证数据库
                )
            elif (config['MONGO_TYPE']=='replica_set'):
                MONGO_URI += f'?replicaSet={config["MONGO_REPLICA_SET"]}'
                self.mongo_client = pymongo.MongoClient(
                    MONGO_URI,
                    readPreference='secondaryPreferred',  # 优先从从节点读取
                    w='majority',  # 写入确认级别
                    retryWrites=True,  # 自动重试写操作
                    socketTimeoutMS=30000,  # 套接字超时时间
                    connectTimeoutMS=20000,  # 连接超时时间
                    serverSelectionTimeoutMS=30000,  # 服务器选择超时时间
                    authSource=config["MONGO_AUTH_DB"],  # 明确指定认证数据库
                )

            # 打印连接字符串，但隐藏密码
            masked_uri = MONGO_URI
            masked_uri = masked_uri.replace(urllib.parse.quote_plus(config["MONGO_PASSWORD"]), "****")
            # 测试连接是否成功
            try:
                # 发送 ping 命令到数据库
                self.mongo_client.admin.command('ping')
                print(f"Connecting to MongoDB: {masked_uri}")
            except Exception as e:
                print(f"MongoDB connection failed: {e}")
                raise
            
            # 有需要再放开
            # self.mysql_conn = mysql.connector.connect(
            #     host=config.MYSQL_HOST,
            #     user=config.MYSQL_USER,
            #     password=config.MYSQL_PASSWORD,
            #     database=config.MYSQL_DATABASE
            # )
            # self.redis_client.py = redis.StrictRedis(
            #     host=config.REDIS_HOST,
            #     port=config.REDIS_PORT,
            #     password=config.REDIS_PASSWORD,
            #     decode_responses=True
            # )
            self.initialized = True

    def mongo_insert(self, db_name, collection_name, document):
        collection = self.get_mongo_collection(db_name, collection_name)
        return collection.insert_one(document).inserted_id

    def mongo_find(self, db_name, collection_name, query, hint=None, sort=None, projection=None):
        """
        Find documents in MongoDB collection

        Args:
            db_name: Database name
            collection_name: Collection name
            query: Query dictionary
            hint: Optional index hint
            sort: Optional sort specification
            projection: Optional projection (field selection)

        Returns:
            List of documents
        """
        collection = self.get_mongo_collection(db_name, collection_name)
        cursor = collection.find(query, projection)  # Adding projection here
        if hint:
            cursor = cursor.hint(hint)
        if sort:
            cursor = cursor.sort(sort)
        return list(cursor)

    def mongo_update(self, db_name, collection_name, query, update):
        collection = self.get_mongo_collection(db_name, collection_name)
        return collection.update_many(query, {'$set': update}).modified_count
    def mongo_update_one(self, db_name, collection_name, query, update, upsert=False, **kwargs):
        collection = self.get_mongo_collection(db_name, collection_name)
        return collection.update_many(
            filter=query,
            update=update,
            upsert=upsert,
            **kwargs
        )

    def mongo_delete(self, db_name, collection_name, query):
        collection = self.get_mongo_collection(db_name, collection_name)
        return collection.delete_many(query).deleted_count

    def get_mongo_collection(self, db_name, collection_name):
        return self.mongo_client[db_name][collection_name]

    def get_mongo_db(self,db_name=DEFAULT_MONGO_DB or "panda"):
        return self.mongo_client[db_name]

    def mongo_insert_many(self, db_name, collection_name, documents):
        collection = self.get_mongo_collection(db_name, collection_name)
        return collection.insert_many(documents).inserted_ids

    def mongo_aggregate(self, db_name, collection_name, aggregation_pipeline):
        collection = self.get_mongo_collection(db_name, collection_name)
        return list(collection.aggregate(aggregation_pipeline)) 
    
    def get_distinct_values(self, db_name, collection_name, field):
        """Get distinct values for a field"""
        collection = self.get_mongo_collection(db_name, collection_name)
        return collection.distinct(field)

    def mongo_find_one(self, db_name, collection_name, query, hint=None, projection=None, sort=None):
        """
        Find a single document in MongoDB collection

        Args:
            db_name: Database name
            collection_name: Collection name
            query: Query dictionary
            hint: Optional index hint
            project: Optional projection dictionary to specify fields to include/exclude

        Returns:
            Single document or None if not found
        """
        collection = self.get_mongo_collection(db_name, collection_name)
        find_args = {}

        if hint:
            find_args['hint'] = hint
        if projection:
            find_args['projection'] = projection
        if sort:
            find_args['sort'] = sort

        return collection.find_one(query, **find_args)