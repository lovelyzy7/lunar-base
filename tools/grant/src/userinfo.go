package main

import (
	"errors"
	"fmt"
	"time"

	"lunar-tear/server/internal/store"
)

// runSetUserInfo updates the account-level fields the Profile page can edit:
// display name, message, level, exp and paid/free gems. Only the fields that
// are actually present in the request change (nil = leave untouched); the whole
// batch runs in one UpdateUser transaction so lunar-tear's own save path
// persists it with the usual version bookkeeping.
func runSetUserInfo(req *request) (int, error) {
	if req.UserID <= 0 {
		return 0, errors.New("user_id must be positive")
	}
	if req.Name == nil && req.Message == nil && req.Level == nil && req.Exp == nil &&
		req.PaidGem == nil && req.FreeGem == nil {
		return 0, errors.New("no user-info fields to update")
	}
	for name, v := range map[string]*int32{
		"level": req.Level, "exp": req.Exp, "paid_gem": req.PaidGem, "free_gem": req.FreeGem,
	} {
		if v != nil && *v < 0 {
			return 0, fmt.Errorf("%s must not be negative", name)
		}
	}

	db, st, err := openDB(req.DBPath)
	if err != nil {
		return 0, err
	}
	defer db.Close()

	now := time.Now().UnixMilli()
	applied := 0
	_, err = st.UpdateUser(req.UserID, func(u *store.UserState) {
		if req.Name != nil {
			u.Profile.Name = *req.Name
			u.Profile.NameUpdateDatetime = now
			applied++
		}
		if req.Message != nil {
			u.Profile.Message = *req.Message
			u.Profile.MessageUpdateDatetime = now
			applied++
		}
		if req.Level != nil {
			u.Status.Level = *req.Level
			applied++
		}
		if req.Exp != nil {
			u.Status.Exp = *req.Exp
			applied++
		}
		if req.PaidGem != nil {
			u.Gem.PaidGem = *req.PaidGem
			applied++
		}
		if req.FreeGem != nil {
			u.Gem.FreeGem = *req.FreeGem
			applied++
		}
	})
	if err != nil {
		return 0, fmt.Errorf("set user info: %w", err)
	}
	return applied, nil
}
