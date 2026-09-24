# Data Retention Policy

## 1 Scope

This policy applies to all customer data processed by Northwind Analytics. It covers production databases, backups, and log archives.

## 2 Ownership

The Data Protection Officer owns this policy. The policy is reviewed once every twelve months.

## 3 Retention

### 3.1 Customer records

Customer account records are retained for the lifetime of the account plus 24 months.

### 3.2 Retention period for logs

Application logs are retained for 90 days. Security audit logs are retained for 365 days.

### 3.3 Backups

Database backups are encrypted with AES-256. Backups are kept for 35 days before deletion.

## 4 Deletion

Deletion requests from customers are fulfilled within 30 days of verification. Deleted data is purged from backups when the backup expires.
